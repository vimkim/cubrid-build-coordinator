"""Ordered build/install operations. Workenv remains the host authority."""
from contextlib import redirect_stdout
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time

from .errors import ActionError, UsageError, command_text
from .locks import Locks, canonical
from .processes import active_install_processes, checked
from . import presets, status, workenv


class Coordinator:
    def __init__(self, env=None):
        self.env = dict(os.environ if env is None else env)
        # Bash repairs a stale inherited PWD on startup. Preserve a valid
        # logical symlink spelling, but never select an unrelated worktree.
        try:
            valid_pwd = os.path.samefile(self.env.get('PWD', ''), os.getcwd())
        except OSError:
            valid_pwd = False
        if not valid_pwd:
            self.env['PWD'] = os.getcwd()
        for key in ('PRESET_MODE', 'CUBRID_BUILD_DIR', 'CUBRID'):
            if not self.env.get(key):
                raise ActionError(f'{key} is required. Load the selected source-worktree environment before retrying.')
        self.locks = Locks(self.env['CUBRID_BUILD_DIR'], self.env['CUBRID'])
        self.request = None
        self.stop_attempts = []
        self.compiled = False

    def run(self, command, phase='command execution'):
        try:
            checked(command, env=self.env)
        except ActionError as error:
            raise ActionError(f'Failure during {phase}.\n{error}\n'
                              f'Build directory: {self.locks.build}\n'
                              f'Installation: {self.locks.install}', error.status) from error

    def retry_command(self):
        if self.request is None:
            return 'cubrid-build-coordinator stop-and-build'
        if self.request.name == 'build' and self.compiled:
            return command_text(['cubrid-build-coordinator', 'install', str(self.request.wait)])
        if self.request.name in ('runtime', 'installation-use'):
            prefix = ['cubrid-build-coordinator', self.request.name, str(self.request.wait)]
            if self.request.database:
                prefix.extend(['--database', self.request.database])
            return command_text(prefix) + ' -- ' + command_text(self.request.command)
        return command_text(['cubrid-build-coordinator', self.request.name, *self.request.arguments])

    def report_owner(self, kind):
        try:
            with redirect_stdout(sys.stderr):
                result = status.main(['--lock', kind])
            if not result:
                return
        except (OSError, ValueError) as error:
            print(f'Lock owner inspection failed: {error}', file=sys.stderr)
        print('Lock owner inspection was incomplete.', file=sys.stderr)

    def build_lock(self):
        acquired, _ = self.locks.acquire('build', self.request.build_wait)
        if not acquired:
            print(f'Build directory is busy: {self.env["CUBRID_BUILD_DIR"]}', file=sys.stderr)
            self.report_owner('build')
            raise ActionError(f'Build lock acquisition timed out after {self.request.build_wait} seconds. '
                              f'No process was stopped. Retry with: {self.retry_command()}', 75)

    def active(self):
        return active_install_processes(self.locks.install)

    def report_busy(self, processes):
        action = self.request.name if self.request else 'stop-and-build'
        print(f'\n{action} deferred: the target CUBRID environment is busy.', file=sys.stderr)
        print(f'Environment: {self.env["CUBRID"]}', file=sys.stderr)
        if processes:
            print('Active processes:', file=sys.stderr)
            for pid, name, executable in processes:
                print(f'  PID {pid:<8} {name:<20} {executable}', file=sys.stderr)
        else:
            print('The runtime lock could not be acquired.', file=sys.stderr)
            self.report_owner('runtime')
        if self.stop_attempts:
            print('Stop commands were already attempted. Some services may now be stopped.\n'
                  'Installation was not attempted. No automatic restart was performed.', file=sys.stderr)
        else:
            print('No process was stopped.', file=sys.stderr)
        print(f'After resolving the busy state, retry with: {self.retry_command()}', file=sys.stderr)

    def runtime_lock(self, wait, *, idle=False):
        acquired, processes = self.locks.acquire('runtime', wait,
                                               idle=self.active if idle else None)
        if acquired:
            return
        if idle:
            self.report_busy(processes)
        else:
            print(f'Runtime command could not acquire the lock within {wait} seconds.', file=sys.stderr)
            print(f'Environment: {self.env["CUBRID"]}\nLock: {self.locks.paths["runtime"]}', file=sys.stderr)
            self.report_owner('runtime')
        raise ActionError(f'Installation lock or idle check did not succeed within {wait} seconds. '
                          f'Retry with: {self.retry_command()}', 75)

    def compile(self):
        presets.verify_build(self.env, self.locks.build)
        try:
            self.run(['cmake', '--build', '--preset', self.env['PRESET_MODE']], 'compilation')
        except ActionError as error:
            raise ActionError(f'{error}\nCompilation failed. Installation was not attempted.',
                              error.status) from error
        self.compiled = True

    def install(self):
        presets.verify_destdir(self.env)
        try:
            self.install_files()
        except (ActionError, OSError) as error:
            raise ActionError(f'Installation failed. Partial changes may remain in {self.locks.install}.\n'
                              f'{error}\nInspect the installation before retrying. '
                              'No automatic rollback or restart was performed.',
                              getattr(error, 'status', 1)) from error

    def install_files(self):
        build_jdk = Path(self.locks.build) / 'vm/jdk8'
        install_jdk = Path(self.locks.install) / 'vm/jdk8'
        if build_jdk.is_symlink() and install_jdk.is_dir() and not install_jdk.is_symlink():
            print(f'Replacing bundled install JDK with JAVA_HOME symlink: {install_jdk}', flush=True)
            self.run(['cmake', '-E', 'remove_directory', str(install_jdk)], 'JDK destination adjustment')
        elif build_jdk.is_dir() and not build_jdk.is_symlink() and install_jdk.is_symlink():
            print(f'Replacing JAVA_HOME install symlink with bundled JDK: {install_jdk}', flush=True)
            self.run(['cmake', '-E', 'remove', str(install_jdk)], 'JDK destination adjustment')
        self.run(['cmake', '--install', self.locks.build, '--prefix', self.locks.install],
                 'installation')
        print('Build and install completed successfully!', flush=True)

    def guidance(self):
        print('Build-only installation is ready. Initialize explicitly when needed:')
        print('  ' + shlex.join(['cub-workenv', 'init', '--worktree', self.env.get('PWD') or os.getcwd(),
                                  '--install', self.env['CUBRID'], '--preset', self.env['PRESET_MODE']]))
        print('Existing work environments are preserved; use cub-workenv doctor after rebuilding.')

    def select(self):
        try:
            self.env = workenv.select(self.env, self.locks.install)
        except ActionError as error:
            raise ActionError(f'Host environment selection failed for {self.locks.install}.\n{error}',
                              error.status) from error

    def stop(self):
        command = str(Path(self.env['CUBRID']) / 'bin/cubrid')
        if os.access(command, os.X_OK):
            with tempfile.TemporaryFile() as output:
                try:
                    checked([command, 'server', 'status'], env=self.env, stdout=output)
                except ActionError as error:
                    output.seek(0)
                    print(os.fsdecode(output.read()), file=sys.stderr, end='')
                    raise ActionError(f'Could not read server status before stopping.\n{error}',
                                      error.status) from error
                output.seek(0)
                names = [fields[1] for line in os.fsdecode(output.read()).splitlines()
                         if len(fields := line.split()) >= 2 and fields[0] in ('Server', 'HA-Server')]
            for name in names:
                workenv.require_database(self.env, name)
                self.stop_attempts.append(f'server {name}')
                self.run([command, 'server', 'stop', name], 'server stop')
            for category in ('service', 'broker'):
                self.stop_attempts.append(category)
                try:
                    self.run([command, category, 'stop'], f'{category} stop')
                except ActionError as error:
                    print(f'Warning: {error}\nContinuing to check remaining native processes.',
                          file=sys.stderr)
        deadline = time.monotonic() + 30
        while processes := self.active():
            if time.monotonic() >= deadline:
                print('The explicitly requested stop did not finish within 30 seconds.', file=sys.stderr)
                self.report_busy(processes)
                raise ActionError('Stop timed out after 30 seconds. Remaining processes were not '
                                  'forcibly killed and installation was not attempted.', 1)
            time.sleep(1)

    def delete(self):
        prefix = self.locks.install.rstrip('/') + '/'
        guarded = [self.env.get('PWD') or os.getcwd(), self.env.get('HOME') or os.path.expanduser('~'),
                   self.env['CUBRID_BUILD_DIR']]
        # Canonical guards additionally prevent alias/.. paths bypassing the
        # original lexical protection. Storage marker discovery does not follow
        # directory symlinks, matching find's default behavior.
        if any(path.rstrip('/') + '/' == prefix or (path.rstrip('/') + '/').startswith(prefix)
               or (canonical(path).rstrip('/') + '/').startswith(prefix) for path in guarded):
            raise UsageError(f'refusing to remove a broad installation path: {self.locks.install}')

        def onerror(error):
            raise error

        for directory, _, files in os.walk(self.locks.install, onerror=onerror):
            for name in files:
                path = Path(directory) / name
                if name.endswith(('_vinf', '_lgat')) and not path.is_symlink() and path.is_file():
                    raise ActionError('Installation contains database storage; preserve it and inspect manually: '
                                      + self.locks.install)
        self.run(['/bin/rm', '-rf', '--', self.locks.install],
                 'installation deletion. Failure may leave a partially deleted installation')

    def execute(self, request):
        """Run a validated action with B before R and retain B across installation."""
        self.request = request
        try:
            self.execute_action(request)
        except (ActionError, OSError):
            if self.stop_attempts:
                print('Stop commands attempted: ' + ', '.join(self.stop_attempts) + '.\n'
                      'Some services may remain stopped. No automatic restart was performed.', file=sys.stderr)
            raise

    def execute_action(self, request):
        action = request.name
        with self.locks:
            if action in ('runtime', 'installation-use'):
                self.runtime_lock(request.wait)
                if action == 'runtime':
                    self.select()
                if request.database:
                    workenv.require_database(self.env, request.database)
                self.run(request.command)
                return

            self.build_lock()
            if action == 'configure':
                presets.verify_build(self.env, self.locks.build, configure=True)
                self.run(['cmake', '--preset', self.env['PRESET_MODE']], 'CMake configuration')
            elif action == 'compile':
                self.compile()
            elif action in ('target', 'install-target'):
                presets.verify_build(self.env, self.locks.build, installation_target=True)
                presets.verify_install_prefix(self.locks.build, self.locks.install)
                self.runtime_lock(request.wait, idle=True)
                self.run(['cmake', '--build', '--preset', self.env['PRESET_MODE'], '--target', request.target],
                         'CMake target execution. A failed installation target may leave partial changes')
            elif action == 'stop-and-build':
                self.compile()
                self.runtime_lock(request.wait)
                self.select()
                presets.verify_destdir(self.env, 'selected host environment')
                self.stop()
                self.install()
                self.guidance()
            else:
                if action == 'build':
                    self.compile()
                self.runtime_lock(request.wait, idle=True)
                if action == 'installation-delete':
                    self.delete()
                else:
                    self.install()
                    self.guidance()
