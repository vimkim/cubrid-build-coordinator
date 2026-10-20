"""Resource identity and non-inheritable Linux flock ownership."""
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import re
import subprocess
import time

from .errors import UsageError


def canonical(path):
    # Keep GNU realpath -m behavior for missing components and symlink loops.
    # -z avoids ambiguity when a filesystem path ends in a newline.
    result = subprocess.run(['realpath', '-m', '-z', '--', os.fspath(path)],
                            stdout=subprocess.PIPE, check=True)
    return os.fsdecode(result.stdout.removesuffix(b'\0'))


def namespace():
    return Path(os.environ.get('XDG_RUNTIME_DIR') or '/tmp') / f'cubrid-dev-locks-{os.getuid()}'


def resource_digest(resource):
    # Bash command substitution removes trailing newlines from realpath's
    # output before hashing. Keep that key quirk during mixed migration, while
    # retaining the actual canonical path for observation and filesystem work.
    return hashlib.sha256(os.fsencode(resource).rstrip(b'\n')).hexdigest()


def lock_path(root, kind, resource):
    return root / f'{kind}-{resource_digest(resource)}.lock'


def timeout_seconds(value):
    if not re.fullmatch(r'[0-9]+', value):
        raise UsageError(f'timeout must be a non-negative integer: {value}')
    try:
        return int(value)
    except ValueError as error:
        raise UsageError(f'timeout is too large: {value}') from error


class Locks:
    def __init__(self, build, install):
        self.root = namespace()
        self.build = canonical(build)
        self.install = canonical(install)
        self.paths = {kind: lock_path(self.root, kind, resource)
                      for kind, resource in [('build', self.build), ('runtime', self.install)]}
        self.descriptors = {}

    def __enter__(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        return self

    def __exit__(self, *_):
        for descriptor in self.descriptors.values():
            os.close(descriptor)
        self.descriptors.clear()

    def acquire(self, kind, seconds, *, idle=None):
        """Return (acquired, active processes), retaining successful locks.

        An optional idle observation runs while the runtime lock is held.
        Failed observations release only that lock, retaining the build lock.
        """
        deadline = time.monotonic() + seconds
        descriptor = os.open(self.paths[kind], os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o666)
        retained = False
        try:
            while True:
                processes = []
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    if idle is not None:
                        processes = idle()
                    if not processes:
                        self.descriptors[kind] = descriptor
                        retained = True
                        return True, []
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, processes
                time.sleep(min(2 if idle is not None else .05, remaining))
        finally:
            if not retained:
                os.close(descriptor)
