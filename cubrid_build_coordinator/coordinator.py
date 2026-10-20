"""Ordered build/install operations. Workenv remains the host authority."""
from contextlib import redirect_stdout
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time

from .errors import ActionError, UsageError
from .locks import Locks, canonical, timeout_seconds
from .processes import active_install_processes, checked
from . import status, workenv


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
                raise ActionError(f'{key} is required')
        self.build_wait = self.env.get('CUBRID_BUILD_LOCK_TIMEOUT') or '600'
        self.runtime_wait = self.env.get('CUBRID_RUNTIME_LOCK_TIMEOUT') or '300'
        self.locks = Locks(self.env['CUBRID_BUILD_DIR'], self.env['CUBRID'])

    def run(self, command):
        checked(command, env=self.env)

    def report_owner(self, kind):
        try:
            with redirect_stdout(sys.stderr):
                result = status.main(['--lock', kind])
            if not result:
                return
        except (OSError, ValueError):
            pass
        print('Lock owner inspection was incomplete.', file=sys.stderr)

    def build_lock(self):
        acquired, _ = self.locks.acquire('build', timeout_seconds(self.build_wait))
        if not acquired:
            print(f'Build directory is busy: {self.env["CUBRID_BUILD_DIR"]}', file=sys.stderr)
            self.report_owner('build')
            raise ActionError('Retry this command later. No process was stopped.', 75)

    def active(self):
        return active_install_processes(self.locks.install)

    def report_busy(self, processes):
        print('\nInstallation deferred: the target CUBRID environment is busy.', file=sys.stderr)
        print(f'Environment: {self.env["CUBRID"]}', file=sys.stderr)
        if processes:
            print('Active processes:', file=sys.stderr)
            for pid, name, executable in processes:
                print(f'  PID {pid:<8} {name:<20} {executable}', file=sys.stderr)
        else:
            print('The runtime lock could not be acquired.', file=sys.stderr)
            self.report_owner('runtime')
        print('No process was stopped. Retry with: just install', file=sys.stderr)

    def runtime_lock(self, wait, *, idle=False):
        acquired, processes = self.locks.acquire('runtime', timeout_seconds(wait),
                                               idle=self.active if idle else None)
        if acquired:
            return
        if idle:
            self.report_busy(processes)
        else:
            print(f'Runtime command could not acquire the lock within {wait} seconds.', file=sys.stderr)
            print(f'Environment: {self.env["CUBRID"]}\nLock: {self.locks.paths["runtime"]}', file=sys.stderr)
            self.report_owner('runtime')
        raise ActionError(status=75)

    def compile(self):
        self.run(['cmake', '--build', '--preset', self.env['PRESET_MODE']])

    def install(self):
        build_jdk = Path(self.env['CUBRID_BUILD_DIR']) / 'vm/jdk8'
        install_jdk = Path(self.locks.install) / 'vm/jdk8'
        if build_jdk.is_symlink() and install_jdk.is_dir() and not install_jdk.is_symlink():
            print(f'Replacing bundled install JDK with JAVA_HOME symlink: {install_jdk}', flush=True)
            self.run(['cmake', '-E', 'remove_directory', str(install_jdk)])
        elif build_jdk.is_dir() and not build_jdk.is_symlink() and install_jdk.is_symlink():
            print(f'Replacing JAVA_HOME install symlink with bundled JDK: {install_jdk}', flush=True)
            self.run(['cmake', '-E', 'remove', str(install_jdk)])
        self.run(['cmake', '--install', self.env['CUBRID_BUILD_DIR'], '--prefix', self.env['CUBRID']])
        print('Build and install completed successfully!', flush=True)

    def guidance(self):
        print('Build-only installation is ready. Initialize explicitly when needed:')
        print('  ' + shlex.join(['cub-workenv', 'init', '--worktree', self.env.get('PWD') or os.getcwd(),
                                  '--install', self.env['CUBRID'], '--preset', self.env['PRESET_MODE']]))
        print('Existing work environments are preserved; use cub-workenv doctor after rebuilding.')

    def select(self):
        self.env = workenv.select(self.env, self.locks.install)

    def stop(self):
        command = str(Path(self.env['CUBRID']) / 'bin/cubrid')
        if os.access(command, os.X_OK):
            with tempfile.TemporaryFile() as output:
                checked([command, 'server', 'status'], env=self.env, stdout=output)
                output.seek(0)
                names = [fields[1] for line in os.fsdecode(output.read()).splitlines()
                         if len(fields := line.split()) >= 2 and fields[0] in ('Server', 'HA-Server')]
            for name in names:
                workenv.require_database(self.env, name)
                self.run([command, 'server', 'stop', name])
            for category in ('service', 'broker'):
                try:
                    self.run([command, category, 'stop'])
                except ActionError:
                    pass
        deadline = time.monotonic() + 30
        while processes := self.active():
            if time.monotonic() >= deadline:
                print('The explicitly requested stop did not finish within 30 seconds.', file=sys.stderr)
                self.report_busy(processes)
                raise ActionError(status=1)
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
        self.run(['/bin/rm', '-rf', '--', self.locks.install])

    def execute(self, action, args):
        """Run a validated action with B before R and retain B across installation."""
        with self.locks:
            if action in ('runtime', 'installation-use'):
                if args and args[0] == '--':
                    wait = self.runtime_wait
                else:
                    wait = (args.pop(0) if args else '') or self.runtime_wait
                database = ''
                if args and args[0] == '--database':
                    if len(args) < 2:
                        raise UsageError('runtime --database requires a database name')
                    _, database = args[:2]
                    args = args[2:]
                if args and args[0] == '--':
                    args = args[1:]
                if not args:
                    raise UsageError('runtime requires a command')
                self.runtime_lock(wait)
                if action == 'runtime':
                    self.select()
                if database:
                    workenv.require_database(self.env, database)
                self.run(args)
                return

            self.build_lock()
            if action == 'configure':
                self.run(['cmake', '--preset', self.env['PRESET_MODE']])
            elif action == 'compile':
                self.compile()
            elif action in ('target', 'install-target'):
                self.runtime_lock((args[1] if len(args) == 2 else '') or '0', idle=True)
                self.run(['cmake', '--build', '--preset', self.env['PRESET_MODE'], '--target', args[0]])
            elif action == 'stop-and-build':
                self.compile()
                self.runtime_lock(self.runtime_wait)
                self.select()
                self.stop()
                self.install()
                self.guidance()
            else:
                if action == 'build':
                    self.compile()
                default = self.runtime_wait if action == 'installation-delete' else '0'
                self.runtime_lock((args[0] if args else '') or default, idle=True)
                if action == 'installation-delete':
                    self.delete()
                else:
                    self.install()
                    self.guidance()
