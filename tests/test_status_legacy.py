#!/usr/bin/env python3
"""Exercise live descriptor ownership without touching a CUBRID installation."""
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from cli_compat import coordinator_command


PROJECT = Path(__file__).resolve().parents[1]
COORDINATOR = Path(os.environ.get('COORDINATOR_UNDER_TEST', PROJECT / 'bin/cubrid-build-coordinator'))
sys.path.insert(0, str(PROJECT))
INSPECTOR_PATH = Path(os.environ.get('INSPECTOR_UNDER_TEST', PROJECT / 'cubrid_build_coordinator/status.py'))
SPEC = importlib.util.spec_from_file_location('lock_status', INSPECTOR_PATH)
INSPECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSPECTOR)


class LockStatusTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cubrid-lock-status-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = dict(os.environ, XDG_RUNTIME_DIR=str(self.root),
                        CUBRID=str(self.root / 'install'),
                        CUBRID_BUILD_DIR=str(self.root / 'build'),
                        PRESET_MODE='debug_gcc', CUBRID_BUILD_LOCK_TIMEOUT='0')

    def invoke(self, *args, **kwargs):
        return subprocess.run([str(COORDINATOR), *args], env=self.env,
                              cwd=self.root, text=True, capture_output=True,
                              timeout=5, **kwargs)

    @contextmanager
    def runtime_holder(self):
        ready = self.root / 'ready'
        ready.unlink(missing_ok=True)
        code = ('import pathlib,time; '
                f'pathlib.Path({str(ready)!r}).touch(); time.sleep(30)')
        holder = subprocess.Popen([str(COORDINATOR), 'installation-use', '0', '--',
                                   sys.executable, '-c', code], env=self.env,
                                  cwd=self.root, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                if holder.poll() is not None:
                    self.fail('Holder failed: ' + holder.stderr.read())
                time.sleep(.01)
            self.assertTrue(ready.exists(), 'Holder did not start')
            yield holder
        finally:
            if holder.poll() is None:
                holder.terminate()
            holder.wait(timeout=3)
            holder.stderr.close()

    def test_idle_inspection_does_not_create_lock_files(self):
        result = self.invoke('locks', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual([lock['state'] for lock in report['locks']], ['idle', 'idle'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_reports_supervisor_holding_descriptor_after_flock_child_exits(self):
        with self.runtime_holder() as holder:
            result = self.invoke('locks', '--json')
            self.assertEqual(result.returncode, 0, result.stderr)
            runtime = next(lock for lock in json.loads(result.stdout)['locks']
                           if lock['kind'] == 'runtime')
            self.assertEqual(runtime['state'], 'held')
            self.assertEqual(runtime['resource'], self.env['CUBRID'])
            self.assertEqual([owner['pid'] for owner in runtime['owners']], [holder.pid])
            owner = runtime['owners'][0]
            self.assertEqual(owner['cwd'], str(self.root))
            self.assertIn('installation-use', owner['command'])
            self.assertGreater(owner['start_ticks'], 0)
            self.assertGreaterEqual(owner['process_age_seconds'], 0)
            self.assertEqual(owner['uid'], os.getuid())
        result = self.invoke('locks', '--json')
        self.assertTrue(all(lock['state'] == 'idle'
                            for lock in json.loads(result.stdout)['locks']))

    def test_busy_install_and_runtime_errors_identify_holder(self):
        with self.runtime_holder() as holder:
            for args in [('install', '0'), ('runtime', '0', '--', 'true')]:
                with self.subTest(args=args):
                    result = self.invoke(*args)
                    self.assertEqual(result.returncode, 75, result.stderr)
                    self.assertIn(f'PID {holder.pid}', result.stderr)
                    self.assertIn('installation-use', result.stderr)
                    self.assertIn(str(self.root), result.stderr)

    def test_all_reports_existing_holders_without_selected_environment(self):
        with self.runtime_holder() as holder:
            self.env.pop('CUBRID')
            self.env.pop('CUBRID_BUILD_DIR')
            self.env.pop('PRESET_MODE')
            result = self.invoke('locks', '--all', '--json')
            self.assertEqual(result.returncode, 0, result.stderr)
            locks = json.loads(result.stdout)['locks']
            self.assertEqual(len(locks), 1)
            self.assertEqual(locks[0]['resource'], str(self.root / 'install'))
            self.assertEqual(locks[0]['owners'][0]['pid'], holder.pid)

    @unittest.skipUnless((Path(os.environ.get('MY_CUBRID_UNDER_TEST', Path.home() / 'my-cubrid')) / 'stow/cubrid/justfile').is_file(),
                         'set MY_CUBRID_UNDER_TEST for shared recipe integration')
    def test_public_just_recipes_report_the_same_owner(self):
        repo = Path(os.environ.get('MY_CUBRID_UNDER_TEST', Path.home() / 'my-cubrid'))
        (self.root / 'justfile').symlink_to(repo / 'stow/cubrid/justfile')
        (self.root / '.just').symlink_to(repo / 'stow/cubrid/.just')
        self.env['MY_CUBRID'] = str(repo)
        with self.runtime_holder() as holder:
            for recipe in ['locks', 'locks-all', 'locks-json']:
                with self.subTest(recipe=recipe):
                    result = subprocess.run(['just', recipe], env=self.env,
                                            cwd=self.root, text=True, capture_output=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if recipe == 'locks-json':
                        report = json.loads(result.stdout)
                        runtime = next(lock for lock in report['locks'] if lock['kind'] == 'runtime')
                        self.assertEqual(runtime['owners'][0]['pid'], holder.pid)
                    else:
                        self.assertIn(f'PID {holder.pid}', result.stdout)

    def test_canonical_install_alias_finds_existing_owner(self):
        with self.runtime_holder() as holder:
            (self.root / 'install').mkdir()
            alias = self.root / 'install-alias'
            alias.symlink_to(self.root / 'install', target_is_directory=True)
            self.env['CUBRID'] = str(alias)
            report = json.loads(self.invoke('locks', '--json').stdout)
            runtime = next(lock for lock in report['locks'] if lock['kind'] == 'runtime')
            self.assertEqual(runtime['resource'], str(self.root / 'install'))
            self.assertEqual(runtime['owners'][0]['pid'], holder.pid)

    def test_opening_same_inode_does_not_make_process_a_holder(self):
        with self.runtime_holder() as holder:
            digest = hashlib.sha256(self.env['CUBRID'].encode()).hexdigest()
            path = self.root / f'cubrid-dev-locks-{os.getuid()}' / f'runtime-{digest}.lock'
            with path.open('r'):
                report = json.loads(self.invoke('locks', '--json').stdout)
                runtime = next(lock for lock in report['locks'] if lock['kind'] == 'runtime')
                self.assertEqual([owner['pid'] for owner in runtime['owners']], [holder.pid])

    def test_owner_replacement_does_not_report_previous_pid(self):
        with self.runtime_holder() as previous:
            previous_pid = previous.pid
        with self.runtime_holder() as current:
            report = json.loads(self.invoke('locks', '--json').stdout)
            runtime = next(lock for lock in report['locks'] if lock['kind'] == 'runtime')
            self.assertEqual([owner['pid'] for owner in runtime['owners']], [current.pid])
            self.assertNotEqual(current.pid, previous_pid)

    def test_changed_lock_is_unknown_without_a_stale_owner(self):
        digest = hashlib.sha256(self.env['CUBRID'].encode()).hexdigest()
        path = self.root / f'runtime-{digest}.lock'
        target = dict(kind='runtime', resource=self.env['CUBRID'], path=str(path), owners=[])
        with path.open('w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)

            def release(*args):
                fcntl.flock(stream, fcntl.LOCK_UN)
                return {}

            with mock.patch.object(INSPECTOR, 'descriptor_owners', side_effect=release):
                INSPECTOR.inspect_locks([target])
        self.assertEqual(target['state'], 'unknown')
        self.assertEqual(target['owners'], [])
        self.assertIn('changed during inspection', target['reason'])

    def test_missing_kernel_visibility_reports_unknown(self):
        target = dict(kind='runtime', resource=self.env['CUBRID'], path=str(self.root / 'lock'), owners=[])
        with mock.patch.object(INSPECTOR, 'PROC', self.root / 'unavailable-proc'):
            INSPECTOR.inspect_locks([target])
        self.assertEqual(target['state'], 'unknown')
        self.assertIn('Cannot read kernel locks', target['reason'])

    def test_owner_command_redacts_common_credentials(self):
        command = ['run', '--password', 'private-one', '--access-token=private-two', 'API_KEY=private-three', 'normal']
        self.assertEqual(INSPECTOR.redact(command),
                         ['run', '--password', '<redacted>', '--access-token=<redacted>', 'API_KEY=<redacted>', 'normal'])

    def test_diagnostic_failure_preserves_temporary_failure_status(self):
        copied = self.root / 'coordinator-without-helper.sh'
        if COORDINATOR.suffix == '.sh':
            copied.write_bytes(COORDINATOR.read_bytes())
        else:
            copied.write_text(
                '#!/usr/bin/env python3\n'
                f'import sys; sys.path.insert(0, {str(PROJECT)!r})\n'
                'from cubrid_build_coordinator import cli, status\n'
                'status.main = lambda *args: 1\n'
                'sys.exit(cli.main())\n')
        copied.chmod(0o755)
        with self.runtime_holder():
            result = subprocess.run([str(copied), 'install', '0'], env=self.env,
                                    cwd=self.root, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 75)
        self.assertIn('Lock owner inspection was incomplete', result.stderr)

    def test_busy_build_error_reports_external_lock_holder(self):
        digest = hashlib.sha256(self.env['CUBRID_BUILD_DIR'].encode()).hexdigest()
        path = self.root / f'cubrid-dev-locks-{os.getuid()}' / f'build-{digest}.lock'
        path.parent.mkdir()
        ready = self.root / 'build-ready'
        code = ('import fcntl,pathlib,time; '
                f'stream=open({str(path)!r}, "w"); '
                'fcntl.flock(stream, fcntl.LOCK_EX); '
                f'pathlib.Path({str(ready)!r}).touch(); time.sleep(30)')
        holder = subprocess.Popen([sys.executable, '-c', code], env=self.env, cwd=self.root)
        try:
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists())
            result = subprocess.run(coordinator_command(COORDINATOR, 'build'), env=self.env,
                                    cwd=self.root, text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 75, result.stderr)
            self.assertIn(f'PID {holder.pid}', result.stderr)
            report = json.loads(self.invoke('locks', '--json').stdout)
            build = next(lock for lock in report['locks'] if lock['kind'] == 'build')
            self.assertEqual(build['state'], 'held')
            self.assertEqual(build['owners'][0]['pid'], holder.pid)
        finally:
            holder.terminate()
            holder.wait(timeout=3)


if __name__ == '__main__':
    unittest.main()
