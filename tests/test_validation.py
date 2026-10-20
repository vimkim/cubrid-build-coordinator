"""Validate action inputs, real CMake resources, and useful failure reports."""
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_acceptance as fixtures


class ActionValidationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AcceptanceTest('test_all_action_traces_match_bash')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_bad_arguments_do_not_run_commands_or_create_locks(self):
        case = self.fixture
        cases = [('build-and-install', '-1'), ('install-target', 'util/install', '1.5'),
                 ('runtime', '0', '--database', '', '--', 'true'),
                 ('installation-use', '0', '--database'), ('target', '')]
        for args in cases:
            with self.subTest(args=args):
                result = case.invoke(*args)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn(args[0], result.stderr)
                self.assertTrue(result.stderr.strip())
                self.assertEqual(case.commands(), [])
                self.assertFalse((case.root / f'cubrid-dev-locks-{os.getuid()}').exists())

    def test_usage_errors_are_reported_before_missing_environment(self):
        env = dict(self.fixture.env)
        env.pop('PRESET_MODE')
        result = self.fixture.invoke('build-and-install', '-1', env=env)
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn('timeout', result.stderr)
        self.assertIn('-1', result.stderr)

    def test_build_ignores_a_busy_installation_and_installation_wait_settings(self):
        case = self.fixture
        env = dict(case.env, CUBRID_RUNTIME_LOCK_TIMEOUT='invalid', DESTDIR='/unused-stage')
        with case.held('runtime'):
            result = case.invoke('build', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(case.commands(), [['cmake', '--build', '--preset', 'debug']])
        self.assertIn('Build completed', result.stdout)
        self.assertNotIn('Installation completed', result.stdout)
        self.assertNotIn('Initialize', result.stdout)

    def test_install_only_does_not_claim_a_build(self):
        result = self.fixture.invoke('install')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.fixture.commands()[0][1], '--install')
        self.assertEqual(len(self.fixture.commands()), 1)
        self.assertIn('Installation completed', result.stdout)
        self.assertNotIn('Build completed', result.stdout)

    def test_retired_names_and_invalid_stop_flags_fail_before_work(self):
        for args, replacement in [(('compile',), 'build'),
                                  (('stop-and-build',), 'build-and-install --stop-runtime'),
                                  (('build', '--stop-runtime'), 'takes no arguments'),
                                  (('install', '--stop-runtime'), 'timeout'),
                                  (('build-and-install', '--stop-runtime', '--stop-runtime'), 'timeout')]:
            with self.subTest(args=args):
                result = self.fixture.invoke(*args)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn(replacement, result.stderr)
                self.assertEqual(self.fixture.commands(), [])
                self.assertFalse((self.fixture.root / f'cubrid-dev-locks-{os.getuid()}').exists())

    def test_stop_option_accepts_explicit_wait_in_either_order(self):
        case = self.fixture
        for args in [('build-and-install', '0', '--stop-runtime'),
                     ('build-and-install', '--stop-runtime', '0')]:
            with self.subTest(args=args):
                case.log.unlink(missing_ok=True)
                result = case.invoke(*args, env=dict(case.env, CUBRID_RUNTIME_LOCK_TIMEOUT='invalid'))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([command[1] for command in case.commands()], ['--build', '--install'])

    def test_busy_stop_retry_retains_explicit_stop_request(self):
        with self.fixture.held('runtime'):
            result = self.fixture.invoke('build-and-install', '0', '--stop-runtime')
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertIn('cubrid-build-coordinator build-and-install 0 --stop-runtime', result.stderr)

    def test_optional_wait_before_database_uses_default(self):
        for action in ('runtime', 'installation-use'):
            with self.subTest(action=action):
                result = self.fixture.invoke(action, '--database', 'testdb', '--', 'true')
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_stop_wait_is_rejected_before_compilation(self):
        env = dict(self.fixture.env, CUBRID_RUNTIME_LOCK_TIMEOUT='invalid')
        result = self.fixture.invoke('build-and-install', '--stop-runtime', env=env)
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertEqual(self.fixture.commands(), [])

    def test_staging_environment_is_rejected_before_building_or_installing(self):
        env = dict(self.fixture.env, DESTDIR=str(self.fixture.root / 'stage'))
        for args in [('build-and-install',), ('install',), ('build-and-install', '--stop-runtime',), ('target', 'install')]:
            with self.subTest(args=args):
                result = self.fixture.invoke(*args, env=env)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn('DESTDIR', result.stderr)
                self.assertEqual(self.fixture.commands(), [])

    def test_silent_child_failure_has_action_command_status_and_redaction(self):
        result = self.fixture.invoke('installation-use', '0', '--', sys.executable,
                                     '-c', 'raise SystemExit(23)', '--password', 'private-value')
        self.assertEqual(result.returncode, 23)
        self.assertIn('installation-use', result.stderr)
        self.assertIn('23', result.stderr)
        self.assertIn(Path(sys.executable).name, result.stderr)
        self.assertNotIn('private-value', result.stderr)
        self.assertNotIn('raise SystemExit(23)', result.stderr)
        attached = self.fixture.invoke('installation-use', '0', '--', sys.executable,
                                       '-craise SystemExit(23) # private-inline-value')
        self.assertEqual(attached.returncode, 23, attached.stderr)
        self.assertNotIn('private-inline-value', attached.stderr)

    def test_signal_failure_names_the_signal(self):
        result = self.fixture.invoke('installation-use', '0', '--', 'sh', '-c', 'kill -TERM $$')
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertIn('SIGTERM', result.stderr)
        self.assertIn('installation-use', result.stderr)

    @unittest.skipUnless(hasattr(signal, 'SIGRTMIN'), 'requires Linux real-time signals')
    def test_realtime_signal_failure_preserves_the_exit_status(self):
        number = int(signal.SIGRTMIN) + 1
        result = self.fixture.invoke('installation-use', '0', '--', sys.executable, '-c',
                                     f'import os; os.kill(os.getpid(), {number})')
        self.assertEqual(result.returncode, 128 + number, result.stderr)
        self.assertIn(f'signal {number}', result.stderr)

    def test_shell_inline_code_is_hidden_in_failure_and_retry_messages(self):
        command = ('bash', '-euc', 'exit 23 # private-inline-content')
        result = self.fixture.invoke('installation-use', '0', '--', *command)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertNotIn('private-inline-content', result.stderr)
        with self.fixture.held('runtime'):
            result = self.fixture.invoke('installation-use', '0', '--', *command)
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertNotIn('private-inline-content', result.stderr)

    def test_silent_selection_failure_has_context(self):
        self.fixture.script('cub-workenv', 'raise SystemExit(7)\n')
        result = self.fixture.invoke('runtime', '0', '--', 'true')
        self.assertEqual(result.returncode, 7)
        self.assertIn('runtime', result.stderr)
        self.assertIn('environment', result.stderr.lower())
        self.assertIn('7', result.stderr)

    def test_busy_retry_preserves_requested_action(self):
        with self.fixture.held('runtime'):
            for args in [('installation-delete', '0'), ('target', 'util/install'),
                         ('install-target', 'util/install', '0')]:
                with self.subTest(args=args):
                    result = self.fixture.invoke(*args)
                    self.assertEqual(result.returncode, 75, result.stderr)
                    self.assertIn('cubrid-build-coordinator ' + ' '.join(args), result.stderr)
                    self.assertNotIn('just install', result.stderr)

    def test_prefix_mismatch_is_reported_before_waiting_for_the_wrong_prefix(self):
        wrong = str(self.fixture.root / 'wrong-install')
        with self.fixture.held('runtime', resource=wrong):
            result = self.fixture.invoke('install-target', 'util/install', '0',
                                         env=dict(self.fixture.env, CUBRID=wrong))
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn('mismatch', result.stderr)
        self.assertIn('CMAKE_INSTALL_PREFIX', result.stderr)
        self.assertEqual(self.fixture.commands(), [])

    def test_stop_timeout_does_not_deny_previous_stop_attempts(self):
        from cubrid_build_coordinator.coordinator import Coordinator
        from cubrid_build_coordinator.errors import ActionError
        case = self.fixture
        case.cubrid_fixture()
        coordinator = Coordinator(case.env)
        coordinator.active = lambda: [(12345, 'fixture', str(case.root / 'install/fixture'))]
        output = io.StringIO()
        with redirect_stderr(output), patch('cubrid_build_coordinator.coordinator.time.monotonic',
                                           side_effect=[0, 31]):
            with self.assertRaises(ActionError):
                coordinator.stop()
        self.assertTrue(any(command[:3] == ['cubrid', 'server', 'stop'] for command in case.commands()))
        self.assertNotIn('No process was stopped', output.getvalue())
        self.assertIn('stop', output.getvalue().lower())

    def test_failed_install_reports_possible_partial_changes(self):
        result = self.fixture.invoke('install', '0', env=dict(self.fixture.env, INSTALL_STATUS='12'))
        self.assertEqual(result.returncode, 12)
        self.assertIn('install', result.stderr)
        self.assertIn('12', result.stderr)
        self.assertIn('partial', result.stderr.lower())

    def test_failed_server_status_retains_captured_diagnostics(self):
        self.fixture.cubrid_fixture()
        (Path(self.fixture.env['CUBRID']) / 'bin/cubrid').write_text(
            f'#!{sys.executable}\nprint("status probe refused")\nraise SystemExit(6)\n')
        result = self.fixture.invoke('build-and-install', '--stop-runtime')
        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertIn('status probe refused', result.stderr)
        self.assertIn('server status', result.stderr)

    def test_stop_selection_cannot_redirect_installation_outside_build_lock(self):
        other = self.fixture.root / 'other-build'
        self.fixture.script('cub-workenv',
                            'print("export CUBRID_RUNTIME_READY=1")\n'
                            f'print("export CUBRID_BUILD_DIR={other}")\n')
        result = self.fixture.invoke('build-and-install', '--stop-runtime')
        self.assertEqual(result.returncode, 0, result.stderr)
        install = self.fixture.commands()[-1]
        self.assertEqual(install[1:3], ['--install', self.fixture.env['CUBRID_BUILD_DIR']])
        self.assertNotIn(str(other), install)

    def test_stop_selection_cannot_enable_staging_before_stop_or_install(self):
        self.fixture.cubrid_fixture()
        self.fixture.script('cub-workenv',
                            'print("export CUBRID_RUNTIME_READY=1")\n'
                            f'print("export DESTDIR={self.fixture.root / "stage"}")\n')
        result = self.fixture.invoke('build-and-install', '--stop-runtime')
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn('DESTDIR', result.stderr)
        self.assertEqual(self.fixture.commands(), [['cmake', '--build', '--preset', 'debug']])


@unittest.skipUnless(shutil.which('cmake') and shutil.which('make'), 'requires CMake and make')
class CMakeResourceValidationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='coordinator-real-cmake-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = dict(os.environ, PRESET_MODE='debug', CUBRID_BUILD_DIR=str(self.root / 'build'),
                        CUBRID=str(self.root / 'install'), XDG_RUNTIME_DIR=str(self.root / 'runtime'),
                        CUBRID_BUILD_LOCK_TIMEOUT='0', CUBRID_RUNTIME_LOCK_TIMEOUT='0',
                        PWD=str(self.root), PYTHONDONTWRITEBYTECODE='1')
        (self.root / 'payload').write_text('probe\n')
        (self.root / 'CMakeLists.txt').write_text(
            'cmake_minimum_required(VERSION 3.21)\nproject(coordinator_probe NONE)\n'
            'add_custom_target(probe ALL COMMAND "${CMAKE_COMMAND}" -E touch '
            '"${CMAKE_BINARY_DIR}/compiled")\ninstall(FILES payload DESTINATION share)\n')
        self.presets = {'version': 3, 'configurePresets': [
            {'name': 'debug', 'generator': 'Unix Makefiles', 'binaryDir': '${sourceDir}/build',
             'cacheVariables': {'CMAKE_INSTALL_PREFIX': '$env{CUBRID}'}}],
            'buildPresets': [{'name': 'debug', 'configurePreset': 'debug'}]}
        self.write_presets()

    def write_presets(self):
        (self.root / 'CMakePresets.json').write_text(json.dumps(self.presets))

    def configure(self):
        subprocess.run(['cmake', '--preset', 'debug'], cwd=self.root, env=self.env,
                       capture_output=True, text=True, timeout=10, check=True)

    def invoke(self, *args, env=None):
        return subprocess.run([sys.executable, '-B', str(fixtures.PYTHON), *args], cwd=self.root,
                              env=env or self.env, capture_output=True, text=True, timeout=10)

    def test_configure_refuses_a_different_preset_directory_before_writing(self):
        result = self.invoke('configure', env=dict(self.env, CUBRID_BUILD_DIR=str(self.root / 'wrong')))
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn('mismatch', result.stderr.lower())
        self.assertIn(str(self.root / 'build'), result.stderr)
        self.assertIn(str(self.root / 'wrong'), result.stderr)
        self.assertFalse((self.root / 'build').exists())
        self.assertFalse((self.root / 'wrong').exists())

    def test_build_refuses_a_different_preset_directory_before_building(self):
        self.configure()
        result = self.invoke('build', env=dict(self.env, CUBRID_BUILD_DIR=str(self.root / 'wrong')))
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertFalse((self.root / 'build/compiled').exists())

    def test_partial_install_refuses_a_different_cached_prefix(self):
        self.configure()
        for action in ('target', 'install-target'):
            with self.subTest(action=action):
                result = self.invoke(action, 'install', env=dict(self.env, CUBRID=str(self.root / 'wrong')))
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn('CMAKE_INSTALL_PREFIX', result.stderr)
                self.assertIn(str(self.root / 'install'), result.stderr)
                self.assertIn(str(self.root / 'wrong'), result.stderr)
                self.assertFalse((self.root / 'install/share/payload').exists())

    def test_staging_prefix_cannot_redirect_a_target_outside_the_install_lock(self):
        self.presets['configurePresets'][0]['cacheVariables']['CMAKE_STAGING_PREFIX'] = '${sourceDir}/stage'
        self.write_presets()
        self.configure()
        result = self.invoke('install-target', 'install', '0')
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn('CMAKE_STAGING_PREFIX', result.stderr)
        self.assertFalse((self.root / 'stage/share/payload').exists())

    def test_preset_staging_environment_cannot_redirect_an_install_target(self):
        self.presets['buildPresets'][0]['environment'] = {'DESTDIR': '${sourceDir}/stage'}
        self.write_presets()
        self.configure()
        result = self.invoke('install-target', 'install', '0')
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn('DESTDIR', result.stderr)
        self.assertFalse(any((self.root / 'stage').rglob('payload')))

    def test_build_preset_can_explicitly_disable_configure_staging(self):
        self.presets['configurePresets'][0]['environment'] = {'DESTDIR': '${sourceDir}/stage'}
        self.write_presets()
        self.configure()
        for settings in ({'environment': {'DESTDIR': ''}}, {'environment': {'DESTDIR': None}},
                         {'inheritConfigureEnvironment': False}):
            with self.subTest(settings=settings):
                self.presets['buildPresets'][0] = {'name': 'debug', 'configurePreset': 'debug', **settings}
                self.write_presets()
                result = self.invoke('install-target', 'install', '0')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((self.root / 'install/share/payload').is_file())
                self.assertFalse(any((self.root / 'stage').rglob('payload')))

    def test_native_tool_options_cannot_change_build_directory_or_stage_installation(self):
        self.configure()
        other = self.root / 'other-build'
        subprocess.run(['cmake', '-S', str(self.root), '-B', str(other)], cwd=self.root,
                       capture_output=True, text=True, timeout=10, check=True)
        for options in (['-C', str(other)], ['--dir=' + str(other)],
                        ['DESTDIR=' + str(self.root / 'stage')],
                        ['$env{NATIVE_DIRECTORY_OPTION}', str(other)]):
            with self.subTest(options=options):
                self.presets['buildPresets'][0]['nativeToolOptions'] = options
                self.write_presets()
                result = self.invoke('build', env=dict(self.env, NATIVE_DIRECTORY_OPTION='-C'))
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn('nativeToolOptions', result.stderr)
                self.assertFalse((other / 'compiled').exists())

    def test_matching_paths_still_configure_build_and_install(self):
        for args in [('configure',), ('build',), ('install-target', 'install', '0')]:
            result = self.invoke(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'install/share/payload').read_text(), 'probe\n')

    def test_canonical_build_and_install_aliases_are_accepted(self):
        self.configure()
        (self.root / 'install').mkdir()
        (self.root / 'build-alias').symlink_to(self.root / 'build', target_is_directory=True)
        (self.root / 'install-alias').symlink_to(self.root / 'install', target_is_directory=True)
        env = dict(self.env, CUBRID_BUILD_DIR=str(self.root / 'build-alias'),
                   CUBRID=str(self.root / 'install-alias'))
        result = self.invoke('install-target', 'install', '0', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'install/share/payload').is_file())

    def test_null_environment_override_uses_the_parent_environment(self):
        preset = self.presets['configurePresets'][0]
        preset['binaryDir'] = '${sourceDir}/build_$env{OUTPUT}'
        preset['environment'] = {'OUTPUT': None}
        self.write_presets()
        build = self.root / 'build_parent'
        result = self.invoke('configure', env=dict(self.env, OUTPUT='parent', CUBRID_BUILD_DIR=str(build)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((build / 'CMakeCache.txt').is_file())

    def test_duplicate_json_keys_are_rejected_with_the_source_file(self):
        path = self.root / 'CMakePresets.json'
        path.write_text('{"version":3,"configurePresets":[],"configurePresets":[]}')
        result = self.invoke('configure')
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn('duplicate JSON key', result.stderr)
        self.assertIn(str(path), result.stderr)
        self.assertFalse((self.root / 'build').exists())

    def test_included_inherited_presets_and_environment_match_real_cmake(self):
        includes = self.root / 'presets'
        includes.mkdir()
        (includes / 'base.json').write_text(json.dumps({
            'version': 4, 'configurePresets': [
                {'name': 'base', 'hidden': True, 'generator': 'Unix Makefiles',
                 'binaryDir': '$env{OUTPUT}',
                 'environment': {'OUTPUT': '${sourceDir}/build_${presetName}'}}]}))
        self.presets = {'version': 4, 'include': ['presets/base.json'],
                        'configurePresets': [{'name': 'cfg', 'inherits': 'base'}],
                        'buildPresets': [{'name': 'debug', 'configurePreset': 'cfg',
                                          'environment': {'OUTPUT': '${sourceDir}/unused'}}]}
        self.write_presets()
        env = dict(self.env, PRESET_MODE='cfg', CUBRID_BUILD_DIR=str(self.root / 'build_cfg'))
        configured = self.invoke('configure', env=env)
        self.assertEqual(configured.returncode, 0, configured.stderr)
        built = self.invoke('build', env=dict(env, PRESET_MODE='debug'))
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertTrue((self.root / 'build_cfg/compiled').exists())

    def test_ambiguous_inherited_fileDir_is_rejected_before_configuration(self):
        includes = self.root / 'presets'
        includes.mkdir()
        (includes / 'base.json').write_text(json.dumps({
            'version': 4, 'configurePresets': [
                {'name': 'base', 'hidden': True, 'generator': 'Unix Makefiles',
                 'binaryDir': '${fileDir}/build'}]}))
        self.presets = {'version': 4, 'include': ['presets/base.json'],
                        'configurePresets': [{'name': 'debug', 'inherits': 'base'}]}
        self.write_presets()
        result = self.invoke('configure')
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn('fileDir', result.stderr)
        self.assertIn('versions', result.stderr)
        self.assertFalse((self.root / 'build').exists())
        self.assertFalse((includes / 'build').exists())

    def test_logical_pwd_is_preserved_when_resolving_sourceDirName(self):
        alias = self.root.parent / (self.root.name + '-alias')
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(alias.unlink)
        self.presets['configurePresets'][0]['binaryDir'] = '${sourceDir}/build_${sourceDirName}'
        self.write_presets()
        build = self.root / ('build_' + alias.name)
        result = self.invoke('configure', env=dict(self.env, PWD=str(alias), CUBRID_BUILD_DIR=str(build)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((build / 'CMakeCache.txt').exists())

    def test_invalid_preset_graphs_print_reasons_without_tracebacks(self):
        for document, reason in [({'version': 3, 'configurePresets': [
                {'name': 'debug', 'inherits': 'debug'}]}, 'cycle'),
                ({'version': 3, 'configurePresets': [
                    {'name': 'debug', 'binaryDir': '$vendor{unknown.path}'}]}, 'unsupported macro'),
                ({'version': 3, 'configurePresets': [
                    {'name': 'debug', 'binaryDir': '$env{A}', 'environment': {'A': '$env{A}'}}]},
                 'environment cycle')]:
            with self.subTest(reason=reason):
                self.presets = document
                self.write_presets()
                result = self.invoke('configure')
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn(reason, result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertFalse((self.root / 'build').exists())


if __name__ == '__main__':
    unittest.main()
