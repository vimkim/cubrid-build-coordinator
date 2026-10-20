#!/usr/bin/env python3
"""A daemon spawned by a runtime command must not retain the coordinator lock."""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile
import time
import unittest

from cli_compat import coordinator_command


PROJECT = Path(__file__).resolve().parents[1]
COORDINATOR = Path(os.environ.get('COORDINATOR_UNDER_TEST', PROJECT / 'bin/cubrid-build-coordinator'))


class RuntimeLockTest(unittest.TestCase):
    def coordinator_environment(
        self,
        root: Path,
        guard_status: int = 0,
        guard_report: str = '{"outcome":"ready"}',
    ) -> dict[str, str]:
        helper_bin = root / 'helpers' / 'bin'
        helper_bin.mkdir(parents=True)
        guard = helper_bin / 'cub-workenv'
        guard.write_text(
            '#!/usr/bin/env bash\n'
            + (f"printf '%s\\n' '{guard_report}' >&2\n" if guard_status else '')
            + "printf '%s\\n' 'export CUBRID_RUNTIME_READY=1'\n"
            + f'exit {guard_status}\n'
        )
        guard.chmod(0o755)
        environment = dict(
            os.environ,
            XDG_RUNTIME_DIR=str(root),
            MY_CUBRID=str(root / 'helpers'),
            CUBRID=str(root / 'install'),
            CUBRID_BUILD_DIR=str(root / 'build'),
            PRESET_MODE='debug',
            CUB_WORKENV_CLI=str(guard),
            CUBRID_DATABASES=str(root / 'databases'),
        )
        environment.pop('CUBRID_RUNTIME_LOCK_HELD', None)
        (root / 'CMakePresets.json').write_text(json.dumps({
            'version': 3, 'configurePresets': [
                {'name': 'debug', 'binaryDir': '$env{CUBRID_BUILD_DIR}'}],
            'buildPresets': [{'name': 'debug', 'configurePreset': 'debug'}]}))
        build = Path(environment['CUBRID_BUILD_DIR'])
        build.mkdir()
        (build / 'CMakeCache.txt').write_text(f'CMAKE_INSTALL_PREFIX:PATH={environment["CUBRID"]}\n')
        return environment

    def test_installation_use_does_not_require_host_initialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.coordinator_environment(root, guard_status=4)
            result = subprocess.run([str(COORDINATOR), 'installation-use', '0', '--',
                'printf', 'container command'], cwd=root, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'container command')

    def test_runtime_action_revalidates_before_running_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'command-ran'
            env = self.coordinator_environment(
                root,
                guard_status=4,
                guard_report='{"outcome":"invalid","diagnostic":{"code":"runtime_invalid"}}',
            )

            for lock_already_held in (False, True):
                with self.subTest(lock_already_held=lock_already_held):
                    if lock_already_held:
                        env['CUBRID_RUNTIME_LOCK_HELD'] = '1'
                    else:
                        env.pop('CUBRID_RUNTIME_LOCK_HELD', None)
                    result = subprocess.run(
                        [
                            str(COORDINATOR), 'runtime', '0', '--', 'python3', '-c',
                            f'import pathlib; pathlib.Path({str(marker)!r}).touch()',
                        ],
                        cwd=root,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )

                    self.assertEqual(result.returncode, 4)
                    self.assertFalse(marker.exists())
                    self.assertIn('runtime_invalid', result.stderr)

    def test_build_and_install_stop_runtime_revalidates_before_stopping_or_installing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command_log = root / 'commands.log'
            command_bin = root / 'commands'
            command_bin.mkdir()
            cmake = command_bin / 'cmake'
            cmake.write_text(
                '#!/usr/bin/env bash\n'
                f'printf \'cmake %s\\n\' "$*" >> {str(command_log)!r}\n'
            )
            cmake.chmod(0o755)
            cubrid = root / 'install' / 'bin' / 'cubrid'
            cubrid.parent.mkdir(parents=True)
            cubrid.write_text(
                '#!/usr/bin/env bash\n'
                f'printf \'cubrid %s\\n\' "$*" >> {str(command_log)!r}\n'
            )
            cubrid.chmod(0o755)
            env = self.coordinator_environment(
                root,
                guard_status=4,
                guard_report='{"outcome":"invalid","diagnostic":{"code":"runtime_invalid"}}',
            )
            env['PATH'] = f'{command_bin}:{env["PATH"]}'

            result = subprocess.run(
                coordinator_command(COORDINATOR, 'build-and-install', '--stop-runtime'),
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(result.returncode, 4, result.stderr)
            self.assertEqual(command_log.read_text().splitlines(), ['cmake --build --preset debug'])
            self.assertIn('runtime_invalid', result.stderr)

    def test_installation_replacement_and_deletion_refuse_native_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.coordinator_environment(root)
            install = root / 'install'
            install.mkdir()
            executable = install / 'native-process'
            shutil.copy2(shutil.which('sleep'), executable)
            running = subprocess.Popen([str(executable), '30'])
            try:
                for action in ('install', 'installation-delete', 'install-target'):
                    args = [action, 'anything', '0'] if action == 'install-target' else [action, '0']
                    result = subprocess.run([str(COORDINATOR), *args], cwd=root, env=env,
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 75, result.stderr)
                    self.assertTrue(executable.exists())
                    self.assertIsNone(running.poll())
            finally:
                running.terminate()
                running.wait(timeout=5)
            result = subprocess.run([str(COORDINATOR), 'installation-delete', '0'], cwd=root,
                                    env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(install.exists())

    def test_runtime_checks_named_registration_without_single_database_assumption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.coordinator_environment(root)
            registry = root / 'databases'
            registry.mkdir()
            (registry / 'databases.txt').write_text('extra /data localhost /log\n')
            for name, expected in [('testdb', 1), ('extra', 0)]:
                marker = root / name
                result = subprocess.run([str(COORDINATOR), 'runtime', '0', '--database', name,
                    '--', 'touch', str(marker)], cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(marker.exists(), expected == 0)

    def test_runtime_lock_environment_marker_cannot_bypass_real_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'command-ran'
            env = self.coordinator_environment(root)
            env['CUBRID_RUNTIME_LOCK_HELD'] = '1'
            runtime_lock_id = hashlib.sha256(str(root / 'install').encode()).hexdigest()
            runtime_lock = (
                root / f'cubrid-dev-locks-{os.getuid()}' / f'runtime-{runtime_lock_id}.lock'
            )
            runtime_lock.parent.mkdir(mode=0o700)

            with runtime_lock.open('w') as lock_stream:
                fcntl.flock(lock_stream, fcntl.LOCK_EX)
                result = subprocess.run(
                    [
                        str(COORDINATOR), 'runtime', '0', '--', 'python3', '-c',
                        f'import pathlib; pathlib.Path({str(marker)!r}).touch()',
                    ],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

            self.assertEqual(result.returncode, 75, result.stderr)
            self.assertFalse(marker.exists())
            self.assertIn('could not acquire the lock', result.stderr)

    def test_competing_compile_actions_are_serialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.coordinator_environment(root)
            command_bin = root / 'commands'
            command_bin.mkdir()
            log = root / 'compile.log'
            cmake = command_bin / 'cmake'
            cmake.write_text('#!/bin/sh\n' +
                f'echo start >> {str(log)!r}\nsleep 0.2\necho end >> {str(log)!r}\n')
            cmake.chmod(0o755)
            env['PATH'] = str(command_bin) + ':' + env['PATH']
            processes = [subprocess.Popen(coordinator_command(COORDINATOR, 'build'), cwd=root, env=env) for _ in range(2)]
            for process in processes:
                self.assertEqual(process.wait(timeout=5), 0)
            self.assertEqual(log.read_text().splitlines(), ['start', 'end', 'start', 'end'])

    def test_full_install_leaves_initialization_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command_log = root / 'commands.log'
            command_bin = root / 'commands'
            command_bin.mkdir()
            cmake = command_bin / 'cmake'
            cmake.write_text(
                '#!/usr/bin/env bash\n'
                f'printf \'cmake %s\\n\' "$*" >> {str(command_log)!r}\n'
            )
            cmake.chmod(0o755)
            env = self.coordinator_environment(root)
            env['PATH'] = f'{command_bin}:{env["PATH"]}'
            runtime_lock_id = hashlib.sha256(str(root / 'install').encode()).hexdigest()
            runtime_lock = (
                root / f'cubrid-dev-locks-{os.getuid()}' / f'runtime-{runtime_lock_id}.lock'
            )
            guard = root / 'helpers' / 'bin' / 'my-cubrid-runtime'
            guard.write_text(
                '#!/usr/bin/env bash\n'
                f'printf \'guard %s\\n\' "$*" >> {str(command_log)!r}\n'
                f'if flock -n {str(runtime_lock)!r} -c true; then exit 88; fi\n'
                'printf \'%s\\n\' \'{"outcome":"ready"}\'\n'
            )

            result = subprocess.run(
                [str(COORDINATOR), 'install', '0'],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            commands = command_log.read_text().splitlines()
            self.assertEqual(commands[0], f'cmake --install {root / "build"} --prefix {root / "install"}')
            self.assertEqual(len(commands), 1)
            self.assertIn('cub-workenv init', result.stdout)

    def test_partial_install_does_not_initialize_guarded_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command_log = root / 'commands.log'
            command_bin = root / 'commands'
            command_bin.mkdir()
            cmake = command_bin / 'cmake'
            cmake.write_text(
                '#!/usr/bin/env bash\n'
                f'printf \'cmake %s\\n\' "$*" >> {str(command_log)!r}\n'
            )
            cmake.chmod(0o755)
            env = self.coordinator_environment(root)
            env['PATH'] = f'{command_bin}:{env["PATH"]}'
            guard = root / 'helpers' / 'bin' / 'my-cubrid-runtime'
            guard.write_text(
                '#!/usr/bin/env bash\n'
                f'printf \'guard %s\\n\' "$*" >> {str(command_log)!r}\n'
            )

            result = subprocess.run(
                [str(COORDINATOR), 'install-target', 'util/install', '0'],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                command_log.read_text().splitlines(),
                ['cmake --build --preset debug --target util/install'],
            )

    def test_install_does_not_call_a_failing_legacy_initializer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command_log = root / 'commands.log'
            installed = root / 'installed'
            command_bin = root / 'commands'
            command_bin.mkdir()
            cmake = command_bin / 'cmake'
            cmake.write_text(
                '#!/usr/bin/env bash\n'
                f'printf \'cmake %s\\n\' "$*" >> {str(command_log)!r}\n'
                f'touch {str(installed)!r}\n'
            )
            cmake.chmod(0o755)
            env = self.coordinator_environment(root)
            env['PATH'] = f'{command_bin}:{env["PATH"]}'
            guard = root / 'helpers' / 'bin' / 'my-cubrid-runtime'
            guard.write_text(
                '#!/usr/bin/env bash\n'
                f'printf \'guard %s\\n\' "$*" >> {str(command_log)!r}\n'
                'exit 4\n'
            )

            result = subprocess.run(
                [str(COORDINATOR), 'install', '0'],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(result.returncode, 0)
            self.assertTrue(installed.exists())
            self.assertEqual(
                command_log.read_text().splitlines(),
                [
                    f'cmake --install {root / "build"} --prefix {root / "install"}',
                ],
            )

    def test_build_failure_prevents_runtime_initialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command_log = root / 'commands.log'
            command_bin = root / 'commands'
            command_bin.mkdir()
            cmake = command_bin / 'cmake'
            cmake.write_text(
                '#!/usr/bin/env bash\n'
                f'printf \'cmake %s\\n\' "$*" >> {str(command_log)!r}\n'
                'exit 9\n'
            )
            cmake.chmod(0o755)
            env = self.coordinator_environment(root)
            env['PATH'] = f'{command_bin}:{env["PATH"]}'
            guard = root / 'helpers' / 'bin' / 'my-cubrid-runtime'
            guard.write_text(
                '#!/usr/bin/env bash\n'
                f'printf \'guard %s\\n\' "$*" >> {str(command_log)!r}\n'
            )

            result = subprocess.run(
                coordinator_command(COORDINATOR, 'build-and-install', '0'),
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(result.returncode, 9)
            self.assertEqual(
                command_log.read_text().splitlines(),
                ['cmake --build --preset debug'],
            )

    def test_foreground_command_keeps_lock_and_exit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.coordinator_environment(root)
            ready = root / 'ready'
            release = root / 'release'
            code = ('import pathlib,time,sys; '
                    f'pathlib.Path({str(ready)!r}).touch(); '
                    f'release=pathlib.Path({str(release)!r})\n'
                    'while not release.exists(): time.sleep(0.01)\n'
                    'sys.exit(23)')
            running = subprocess.Popen([str(COORDINATOR), 'runtime', '0', '--', 'python3', '-c', code], env=env)
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists(), 'Foreground command did not start')
                probe = subprocess.run([str(COORDINATOR), 'runtime', '0', '--', 'true'],
                                       env=env, capture_output=True, timeout=5)
                self.assertEqual(probe.returncode, 75, 'Lock released before foreground command finished')
            finally:
                release.touch()
                running.wait(timeout=5)
            self.assertEqual(running.returncode, 23)

    def test_daemon_does_not_retain_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.coordinator_environment(root)
            pidfile = root / 'daemon.pid'
            spawn = (
                'import pathlib, subprocess; '
                'p = subprocess.Popen(["sleep", "30"], close_fds=False, start_new_session=True, '
                'stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); '
                f'pathlib.Path({str(pidfile)!r}).write_text(str(p.pid))'
            )
            try:
                subprocess.run([str(COORDINATOR), 'runtime', '0', '--', 'python3', '-c', spawn],
                               env=env, check=True, capture_output=True, timeout=5)
                probe = subprocess.run([str(COORDINATOR), 'runtime', '0', '--', 'true'],
                                       env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(probe.returncode, 0,
                                 'A background child retained the runtime lock: ' + probe.stderr)
            finally:
                if pidfile.exists():
                    os.kill(int(pidfile.read_text()), signal.SIGTERM)

    def test_supervisor_signal_keeps_lock_until_harness_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.coordinator_environment(root)
            ready, cleaning, release = (root / p for p in ('ready', 'cleaning', 'release'))
            script = root / 'harness.py'
            script.write_text('import signal,time,pathlib,sys\n'
                'def cleanup(signum, frame):\n'
                f' pathlib.Path({str(cleaning)!r}).touch()\n'
                f' while not pathlib.Path({str(release)!r}).exists(): time.sleep(0.01)\n'
                ' sys.exit(17)\n'
                'signal.signal(signal.SIGTERM, cleanup)\n'
                f'pathlib.Path({str(ready)!r}).touch()\n'
                'while True: time.sleep(0.01)\n')
            child = subprocess.Popen([str(COORDINATOR), 'installation-use', '0', '--',
                                      'python3', str(script)], cwd=root, env=env)
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists())
                child.terminate()
                while not cleaning.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(cleaning.exists(), 'signal did not reach the harness')
                probe = subprocess.run([str(COORDINATOR), 'installation-use', '0', '--', 'true'],
                                       cwd=root, env=env, capture_output=True)
                self.assertEqual(probe.returncode, 75)
            finally:
                release.touch()
                child.wait(timeout=5)
            self.assertEqual(child.returncode, 17)

    def test_runtime_command_retains_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.coordinator_environment(root)
            result = subprocess.run([str(COORDINATOR), 'runtime', '0', '--', 'cat'],
                cwd=root, env=env, input='interactive input\n', capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'interactive input\n')


if __name__ == '__main__':
    unittest.main()
