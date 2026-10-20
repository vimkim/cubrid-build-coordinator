#!/usr/bin/env python3
"""Read coordinator lock ownership from Linux locks and live file descriptors."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import sys
import time


PROC = Path('/proc')
LOCK_NAME = re.compile(r'(build|runtime)-([a-f0-9]{64})\.lock\Z')


def lock_rows(text):
    """Match /proc/locks and fdinfo rows, excluding pending lock requests."""
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if '->' in fields or 'FLOCK' not in fields:
            continue
        position = fields.index('FLOCK')
        try:
            major, minor, inode = fields[position + 4].split(':')
            identity = (int(major, 16), int(minor, 16), int(inode))
            record = tuple(fields[position + 2:position + 5])
        except (ValueError, IndexError):
            continue
        result.setdefault(identity, set()).add(record)
    return result


def file_identity(path):
    info = path.stat()
    return (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino)


def start_ticks(process):
    # The comm field can contain spaces and parentheses. Fields after it are fixed.
    return int((process / 'stat').read_text().rsplit(')', 1)[1].split()[19])


def redact(command):
    result = []
    hide_next = False
    sensitive = re.compile(r'(^|[-_])(password|passwd|token|secret|api[-_]?key|access[-_]?key)$', re.I)
    for argument in command:
        key, separator, _ = argument.partition('=')
        if hide_next:
            result.append('<redacted>')
            hide_next = False
        elif sensitive.search(key) or key == '-p':
            result.append(key + '=<redacted>' if separator else argument)
            hide_next = not separator
        else:
            result.append(argument)
    return result


def read_link(path):
    try:
        return os.readlink(path)
    except OSError:
        return None


def process_details(process, ticks, uptime):
    uid = process.stat().st_uid
    try:
        user = pwd.getpwuid(uid).pw_name
    except KeyError:
        user = str(uid)
    try:
        command = [os.fsdecode(value) for value in (process / 'cmdline').read_bytes().split(b'\0') if value]
    except OSError:
        command = []
    selected = {}
    try:
        for entry in (process / 'environ').read_bytes().split(b'\0'):
            key, separator, value = entry.partition(b'=')
            if separator and key in (b'CUBRID', b'CUBRID_BUILD_DIR'):
                selected[os.fsdecode(key)] = os.path.realpath(os.fsdecode(value))
    except OSError:
        pass
    age = max(0, uptime - ticks / os.sysconf('SC_CLK_TCK'))
    tty = read_link(process / 'fd/0')
    return {'pid': int(process.name), 'uid': uid, 'user': user,
            'start_ticks': ticks, 'process_age_seconds': round(age, 2),
            'process_started_at': datetime.fromtimestamp(time.time() - age, timezone.utc).isoformat(timespec='seconds'),
            'cwd': read_link(process / 'cwd'),
            'tty': tty if tty and tty.startswith('/dev/') else None,
            'command': redact(command)}, selected


def descriptor_owners(wanted, snapshot):
    """Find holders of each locked open file description, not mere file openers."""
    owners = {identity: [] for identity in wanted}
    uptime = float((PROC / 'uptime').read_text().split()[0])
    for process in PROC.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            ticks = start_ticks(process)
            matches = {}
            for descriptor in (process / 'fd').iterdir():
                try:
                    identity = file_identity(descriptor)
                    if identity in wanted:
                        rows = lock_rows((process / 'fdinfo' / descriptor.name).read_text())
                        if rows.get(identity, set()) & snapshot[identity]:
                            matches.setdefault(identity, []).append(descriptor.name)
                except OSError:
                    continue
            if not matches:
                continue
            details, selected = process_details(process, ticks, uptime)
            if start_ticks(process) != ticks:
                continue
            for identity, descriptors in matches.items():
                verified = []
                for descriptor in descriptors:
                    try:
                        if file_identity(process / 'fd' / descriptor) != identity:
                            continue
                        rows = lock_rows((process / 'fdinfo' / descriptor).read_text())
                        if rows.get(identity, set()) & snapshot[identity]:
                            verified.append(int(descriptor))
                    except OSError:
                        continue
                if verified and start_ticks(process) == ticks:
                    owners[identity].append((dict(details, fds=sorted(verified)), selected))
        except (OSError, ValueError, IndexError):
            continue
    return owners


def inspect_locks(targets):
    if not targets:
        return
    try:
        before = lock_rows((PROC / 'locks').read_text())
    except OSError as error:
        for target in targets:
            target.update(state='unknown', reason=f'Cannot read kernel locks: {error}')
        return
    identities = {}
    for index, target in enumerate(targets):
        try:
            identities[index] = file_identity(Path(target['path']))
            target['state'] = 'held' if identities[index] in before else 'idle'
        except FileNotFoundError:
            target['state'] = 'idle'
        except OSError as error:
            target.update(state='unknown', reason=str(error))
    wanted = {identity for identity in identities.values() if identity in before}
    owners = descriptor_owners(wanted, before) if wanted else {}
    after = lock_rows((PROC / 'locks').read_text())
    for index, identity in identities.items():
        target = targets[index]
        try:
            unchanged = file_identity(Path(target['path'])) == identity
        except OSError:
            unchanged = False
        if not unchanged or before.get(identity) != after.get(identity):
            target.update(state='unknown', reason='Lock changed during inspection. Retry the status command.')
            continue
        for owner, selected in owners.get(identity, []):
            target['owners'].append(owner)
            resource = selected.get('CUBRID_BUILD_DIR' if target['kind'] == 'build' else 'CUBRID')
            if resource and hashlib.sha256(os.fsencode(resource)).hexdigest() == LOCK_NAME.fullmatch(Path(target['path']).name)[2]:
                target['resource'] = resource
        target['owners'].sort(key=lambda owner: owner['pid'])
        if target['state'] == 'held' and not target['owners']:
            target['reason'] = 'Lock is held, but no live descriptor holder was readable. Visibility or ownership may have changed.'


def targets_for(args, namespace):
    if args.all:
        try:
            paths = sorted(namespace.iterdir())
        except FileNotFoundError:
            paths = []
        return [{'kind': match[1], 'resource': None, 'path': str(path), 'state': 'unknown', 'owners': []}
                for path in paths if (match := LOCK_NAME.fullmatch(path.name))]
    result = []
    for kind in ([args.lock] if args.lock else ['build', 'runtime']):
        variable = 'CUBRID_BUILD_DIR' if kind == 'build' else 'CUBRID'
        if not os.environ.get(variable):
            raise ValueError(f'{variable} is required for selected lock inspection. Use --all without a selected environment.')
        resource = os.path.realpath(os.environ[variable])
        digest = hashlib.sha256(os.fsencode(resource)).hexdigest()
        result.append({'kind': kind, 'resource': resource,
                       'path': str(namespace / f'{kind}-{digest}.lock'), 'state': 'unknown', 'owners': []})
    return result


def terminal_text(value):
    return ''.join(f'\\x{ord(character):02x}' if ord(character) < 32 or 127 <= ord(character) <= 159
                   else character for character in str(value))


def print_report(report):
    if not report['locks']:
        print(f'No held coordinator locks in {terminal_text(report["namespace"])}')
    for target in report['locks']:
        print(f'{target["kind"].capitalize()} lock: {target["state"]}')
        print(f'  Resource: {terminal_text(target["resource"] or "unavailable")}')
        print(f'  Lock file: {terminal_text(target["path"])}')
        for owner in target['owners']:
            print(f'  Holder: PID {owner["pid"]}, user {terminal_text(owner["user"])}, FD {owner["fds"]}')
            print(f'    Process age: {timedelta(seconds=int(owner["process_age_seconds"]))}, started {owner["process_started_at"]}')
            print(f'    Working directory: {terminal_text(owner["cwd"] or "unavailable")}')
            if owner['tty']:
                print(f'    Terminal: {terminal_text(owner["tty"])}')
            print(f'    Command: {terminal_text(shlex.join(owner["command"]) or "unavailable")}')
        if target.get('reason'):
            print(f'  {terminal_text(target["reason"])}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument('--all', action='store_true', help='show held locks in this user/runtime namespace')
    scope.add_argument('--lock', choices=['build', 'runtime'], help='inspect one selected lock')
    parser.add_argument('--json', action='store_true', help='emit schema-versioned JSON')
    args = parser.parse_args()
    namespace = Path(os.environ.get('XDG_RUNTIME_DIR') or '/tmp') / f'cubrid-dev-locks-{os.getuid()}'
    try:
        targets = targets_for(args, namespace)
        inspect_locks(targets)
    except (OSError, ValueError) as error:
        if args.json:
            print(json.dumps({'schema_version': 1, 'namespace': str(namespace), 'locks': [], 'error': str(error)}))
        else:
            print(f'Lock inspection unavailable: {error}', file=sys.stderr)
        return 1
    if args.all:
        targets = [target for target in targets if target['state'] != 'idle']
    report = {'schema_version': 1, 'namespace': str(namespace), 'locks': targets}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return int(any(target['state'] == 'unknown' for target in targets))


if __name__ == '__main__':
    sys.exit(main())
