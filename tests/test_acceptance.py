"""Cross-process acceptance checks using disposable commands and installations."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from cubrid_build_coordinator.locks import canonical

PYTHON = PROJECT / 'bin/cubrid-build-coordinator'
BASH = PROJECT / 'tests/fixtures/bash/cubrid-build-coordinator.sh'


class AcceptanceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='coordinator-acceptance-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / 'commands'
        self.bin.mkdir()
        self.log = self.root / 'commands.jsonl'
        self.env = dict(os.environ, XDG_RUNTIME_DIR=str(self.root), PWD=str(self.root), HOME=str(self.root / 'home'),
                        CUBRID=str(self.root / 'install'), CUBRID_BUILD_DIR=str(self.root / 'build'),
                        PRESET_MODE='debug', CUBRID_BUILD_LOCK_TIMEOUT='0', CUBRID_RUNTIME_LOCK_TIMEOUT='0',
                        CUBRID_DATABASES=str(self.root / 'databases'),
                        PATH=f'{self.bin}:{os.environ["PATH"]}', COMMAND_LOG=str(self.log))
        for key in ('CUBRID', 'CUBRID_BUILD_DIR', 'CUBRID_DATABASES', 'HOME'):
            Path(self.env[key]).mkdir()
        (Path(self.env['CUBRID_DATABASES']) / 'databases.txt').write_text(
            'testdb /data localhost /log\nextra /data localhost /log\n')
        self.script('cmake', '''import json,os,pathlib,shutil,sys,time
args=sys.argv[1:]
with open(os.environ['COMMAND_LOG'],'a') as stream: stream.write(json.dumps(['cmake',*args])+'\\n')
if args[:2] == ['-E','remove_directory']: shutil.rmtree(args[2])
elif args[:2] == ['-E','remove']: pathlib.Path(args[2]).unlink()
if '--build' in args:
    time.sleep(float(os.environ.get('COMPILE_DELAY','0')))
    sys.exit(int(os.environ.get('COMPILE_STATUS','0')))
sys.exit(int(os.environ.get('INSTALL_STATUS','0')))
''')
        self.env['CUB_WORKENV_CLI'] = str(self.script('cub-workenv', '''import os
print('export CUBRID_RUNTIME_READY=1')
'''))

    def script(self, name, code):
        path = self.bin / name
        path.write_text(f'#!{sys.executable}\n' + code)
        path.chmod(0o755)
        return path

    def invoke(self, *args, cli=PYTHON, env=None, **kwargs):
        return subprocess.run([str(cli), *map(str, args)], env=env or self.env,
                              cwd=self.root, capture_output=True, text=True, timeout=8, **kwargs)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def lock_path(self, kind, resource=None):
        resource = resource or self.env['CUBRID_BUILD_DIR' if kind == 'build' else 'CUBRID']
        digest = hashlib.sha256(os.fsencode(canonical(resource))).hexdigest()
        return self.root / f'cubrid-dev-locks-{os.getuid()}' / f'{kind}-{digest}.lock'

    @contextmanager
    def held(self, kind, resource=None):
        path = self.lock_path(kind, resource)
        path.parent.mkdir(exist_ok=True)
        with path.open('w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def wait_for(self, condition, message='fixture did not become ready', timeout=5):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(condition(), message)

    @contextmanager
    def holder(self, cli=PYTHON, action='installation-use', env=None):
        ready = self.root / 'holder-ready'
        ready.unlink(missing_ok=True)
        code = f'import pathlib,time; pathlib.Path({str(ready)!r}).touch(); time.sleep(30)'
        child = subprocess.Popen([str(cli), action, '0', '--', sys.executable, '-c', code],
                                 cwd=self.root, env=env or self.env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.wait_for(ready.exists)
            yield child
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)

    def test_gnu_resource_identity_including_missing_components_and_symlinks(self):
        target = self.root / 'target'
        target.mkdir()
        (self.root / 'alias').symlink_to(target)
        (self.root / 'loop').symlink_to('loop')
        (self.root / 'dangling').symlink_to('missing/../target')
        paths = ['alias/../target/not-yet/../install', 'no-such/../target', 'dangling/child',
                 'loop/child', '//tmp/../tmp', 'trailing-newline\n', 'odd-\udcff']
        for path in paths:
            with self.subTest(path=path):
                full = self.root / path if not path.startswith('/') else Path(path)
                expected = subprocess.check_output(['realpath', '-m', '-z', '--', os.fsencode(full)])[:-1]
                env = dict(self.env, CUBRID=os.fspath(full))
                result = self.invoke('installation-use','0','--','true',env=env)
                self.assertEqual(result.returncode,0,result.stderr)
                digest = hashlib.sha256(expected.rstrip(b'\n')).hexdigest()
                self.assertTrue((self.root / f'cubrid-dev-locks-{os.getuid()}' /
                                 f'runtime-{digest}.lock').is_file())
                report = json.loads(self.invoke('locks','--json',env=env).stdout)
                runtime = next(lock for lock in report['locks'] if lock['kind']=='runtime')
                self.assertEqual(os.fsencode(runtime['resource']),expected)

    def test_all_action_traces_match_bash(self):
        cases = [(['configure'], [['cmake','--preset','debug']]),
                 (['compile'], [['cmake','--build','--preset','debug']]),
                 (['target','target with spaces'], [['cmake','--build','--preset','debug','--target','target with spaces']]),
                 (['install-target','util/install','0'], [['cmake','--build','--preset','debug','--target','util/install']]),
                 (['install','0'], [['cmake','--install',self.env['CUBRID_BUILD_DIR'],'--prefix',self.env['CUBRID']]]),
                 (['build','0'], [['cmake','--build','--preset','debug'],
                                  ['cmake','--install',self.env['CUBRID_BUILD_DIR'],'--prefix',self.env['CUBRID']]]),
                 (['stop-and-build'], [['cmake','--build','--preset','debug'],
                                       ['cmake','--install',self.env['CUBRID_BUILD_DIR'],'--prefix',self.env['CUBRID']]])]
        for cli in (BASH, PYTHON):
            for args, expected in cases:
                with self.subTest(cli=cli, args=args):
                    self.log.unlink(missing_ok=True)
                    result = self.invoke(*args, cli=cli)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.commands(), expected)

    def test_usage_and_child_status_match_bash(self):
        cases = [([],64), (['wat'],64), (['compile','extra'],64), (['target'],64),
                 (['install-target'],64), (['runtime','0','--'],64),
                 (['runtime','-1','--','true'],64), (['install','1.5'],64),
                 (['runtime','--database','extra','--','true'],64),
                 (['installation-use','0','--','sh','-c','exit 23'],23),
                 (['installation-use','0','--','no-such-coordinator-test-command'],1)]
        for cli in (BASH, PYTHON):
            for args, expected in cases:
                with self.subTest(cli=cli, args=args):
                    self.assertEqual(self.invoke(*args, cli=cli).returncode, expected)

    def test_install_defaults_remain_zero_when_runtime_wait_is_overridden(self):
        self.env['CUBRID_RUNTIME_LOCK_TIMEOUT'] = '100'
        with self.held('runtime'):
            for cli in (BASH, PYTHON):
                for args in [('build',), ('install',), ('install-target','util/install')]:
                    with self.subTest(cli=cli, args=args):
                        result = self.invoke(*args, cli=cli)
                        self.assertEqual(result.returncode, 75, result.stderr)
        result = self.invoke('install')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_compile_then_defer_and_install_only_retry(self):
        for cli in (BASH, PYTHON):
            self.log.unlink(missing_ok=True)
            with self.held('runtime'):
                result = self.invoke('build', '0', cli=cli)
                self.assertEqual(result.returncode, 75, result.stderr)
                self.assertEqual(self.commands(), [['cmake','--build','--preset','debug']])
            result = self.invoke('install', '0', cli=cli)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(self.commands()), 2)
            self.assertEqual(self.commands()[1][1], '--install')

    def test_both_implementations_contend_on_aliases_and_shared_installs(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.env['CUBRID'])
        for holder_cli, contender_cli in ((BASH,PYTHON),(PYTHON,BASH)):
            with self.subTest(holder=holder_cli), self.holder(holder_cli):
                env = dict(self.env, CUBRID=str(alias / 'missing' / '..'),
                           CUBRID_BUILD_DIR=str(self.root / 'different-build'))
                for args in [('installation-use','0','--','true'),('install','0'),
                             ('target','arbitrary'),('installation-delete','0')]:
                    result = self.invoke(*args, cli=contender_cli, env=env)
                    self.assertEqual(result.returncode, 75, result.stderr)
                distinct = dict(env, CUBRID=str(self.root / 'different-install'))
                self.assertEqual(self.invoke('installation-use','0','--','true',
                                             cli=contender_cli,env=distinct).returncode,0)

    def test_mixed_callers_preserve_bash_trailing_newline_hash_quirk(self):
        install = self.root/'install-with-newline\n'
        install.mkdir()
        self.env['CUBRID'] = str(install)
        for first,second in ((BASH,PYTHON),(PYTHON,BASH)):
            with self.subTest(first=first), self.holder(first):
                result = self.invoke('installation-use','0','--','true',cli=second)
                self.assertEqual(result.returncode,75,result.stderr)

    def test_mixed_build_serialization_and_distinct_build_independence(self):
        self.env['COMPILE_DELAY'] = '.5'
        for first, second in ((BASH,PYTHON),(PYTHON,BASH)):
            self.log.unlink(missing_ok=True)
            child = subprocess.Popen([str(first),'compile'],env=self.env,cwd=self.root)
            try:
                self.wait_for(lambda: bool(self.commands()))
                result = self.invoke('compile',cli=second)
                self.assertEqual(result.returncode,75,result.stderr)
                env = dict(self.env, CUBRID_BUILD_DIR=str(self.root/'other-build'))
                self.assertEqual(self.invoke('compile',cli=second,env=env).returncode,0)
            finally:
                child.wait(timeout=5)

    def test_build_lock_retained_while_waiting_for_runtime(self):
        for cli, contender in ((BASH,PYTHON),(PYTHON,BASH)):
            with self.subTest(cli=cli), self.held('runtime'):
                self.log.unlink(missing_ok=True)
                child = subprocess.Popen([str(cli),'build','2'],env=self.env,cwd=self.root,
                                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                try:
                    self.wait_for(lambda: bool(self.commands()))
                    self.assertEqual(self.invoke('compile',cli=contender).returncode,75)
                    self.assertIsNone(child.poll())
                finally:
                    child.wait(timeout=5)
                self.assertEqual(child.returncode,75)

    def test_runtime_wait_does_not_limit_command_execution(self):
        result = self.invoke('installation-use','0','--',sys.executable,'-c',
                             'import time;time.sleep(.2);print("finished")')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,'finished\n')

    def test_workenv_shell_cleanup_source_and_streams(self):
        artifact = self.root / 'environment with spaces.sh'
        artifact.write_text('unset OLD_VALUE LD_PRELOAD\n'
                            'export CUBRID_RUNTIME_READY=1\n'
                            'export SELECTED="value with spaces and ="\n'
                            'printf "selector-output\\n"\n')
        self.env['OLD_VALUE'] = 'inherited'
        self.script('cub-workenv', f'''import shlex
print('source ' + shlex.quote({str(artifact)!r}))
''')
        code = 'import json,os;print(json.dumps([os.getenv("SELECTED"),os.getenv("OLD_VALUE")]))'
        result = self.invoke('runtime','0','--',sys.executable,'-c',code)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.splitlines(),['selector-output','["value with spaces and =", null]'])

    def test_stale_inherited_pwd_is_repaired_before_selection(self):
        self.env['PWD'] = '/unrelated-worktree'
        self.script('cub-workenv', '''import sys,shlex
print('export CUBRID_RUNTIME_READY=1')
print('export SELECTED_WORKTREE=' + shlex.quote(sys.argv[sys.argv.index('--worktree')+1]))
''')
        result = self.invoke('runtime','0','--','sh','-c','printf "%s" "$SELECTED_WORKTREE"')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,str(self.root))

    def test_uninitialized_or_mismatched_selection_never_executes(self):
        for emitted, expected in [('export CUBRID_RUNTIME_READY=0',1),
                                  ('export CUBRID_RUNTIME_READY=0; return 0',1),
                                  ('export CUBRID_RUNTIME_READY=1; export CUBRID=/different-install',1),
                                  ('return 8',8)]:
            with self.subTest(emitted=emitted):
                self.script('cub-workenv',f'print({emitted!r})\n')
                marker = self.root/'command-ran'
                result = self.invoke('runtime','0','--','touch',marker)
                self.assertEqual(result.returncode,expected,result.stderr)
                self.assertFalse(marker.exists())

    def test_arguments_are_not_shell_interpreted(self):
        args = ['space space','',"quote'\"",'$(touch BAD)','a\nb','--password=opaque']
        code = 'import json,sys;print(json.dumps(sys.argv[1:]))'
        result = self.invoke('installation-use','0','--',sys.executable,'-c',code,*args)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),args)
        self.assertFalse((self.root/'BAD').exists())

    def test_terminal_input_and_output_are_preserved(self):
        master, slave = pty.openpty()
        child = subprocess.Popen([str(PYTHON),'installation-use','0','--',sys.executable,'-c',
                                  'import sys; print("READY",flush=True); print("GOT:"+input(),flush=True)'],
                                 env=self.env,cwd=self.root,stdin=slave,stdout=slave,stderr=slave)
        os.close(slave)
        try:
            output=b''
            deadline=time.monotonic()+5
            while b'READY' not in output and time.monotonic()<deadline:
                if select.select([master],[],[],.1)[0]:
                    output+=os.read(master,4096)
            self.assertIn(b'READY',output)
            os.write(master,b'editor input\n')
            self.assertEqual(child.wait(timeout=5),0)
            while select.select([master],[],[],.05)[0]:
                try:
                    output+=os.read(master,4096)
                except OSError:
                    break
            self.assertIn(b'GOT:editor input',output)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            os.close(master)

    def test_term_int_hup_forwarding_retains_lock_through_cleanup(self):
        for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):
            with self.subTest(sig=sig):
                ready, cleaning, release = [self.root/name for name in ('ready','cleaning','release')]
                for path in (ready,cleaning,release):
                    path.unlink(missing_ok=True)
                harness = self.script('harness',f'''import signal,pathlib,time,sys
def cleanup(sig,frame):
    pathlib.Path({str(cleaning)!r}).touch()
    while not pathlib.Path({str(release)!r}).exists(): time.sleep(.01)
    sys.exit(17)
for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP): signal.signal(sig,cleanup)
pathlib.Path({str(ready)!r}).touch()
while True: time.sleep(.01)
''')
                child = subprocess.Popen([str(PYTHON),'installation-use','0','--',str(harness)],
                                         cwd=self.root,env=self.env)
                try:
                    self.wait_for(ready.exists)
                    child.send_signal(sig)
                    self.wait_for(cleaning.exists)
                    self.assertEqual(self.invoke('installation-use','0','--','true').returncode,75)
                    self.assertEqual(self.invoke('install','0').returncode,75)
                finally:
                    release.touch()
                    child.wait(timeout=5)
                self.assertEqual(child.returncode,17)
                self.assertEqual(self.invoke('installation-use','0','--','true').returncode,0)

    def test_signal_termination_exit_status(self):
        for sig in ('TERM','INT','HUP'):
            result = self.invoke('installation-use','0','--','sh','-c',f'kill -{sig} $$')
            self.assertEqual(result.returncode,128+getattr(signal,'SIG'+sig))

    def test_jdk_directory_and_symlink_transitions(self):
        build = Path(self.env['CUBRID_BUILD_DIR'])/'vm/jdk8'
        installed = Path(self.env['CUBRID'])/'vm/jdk8'
        java = self.root/'external-java'
        java.mkdir()
        build.parent.mkdir()
        installed.parent.mkdir()
        build.symlink_to(java)
        installed.mkdir()
        (installed/'bundled').touch()
        self.assertEqual(self.invoke('install','0').returncode,0)
        self.assertFalse(installed.exists())
        self.assertEqual(self.commands()[0],['cmake','-E','remove_directory',str(installed)])
        build.unlink()
        build.mkdir()
        installed.symlink_to(java)
        self.log.unlink()
        self.assertEqual(self.invoke('install','0').returncode,0)
        self.assertFalse(installed.is_symlink())
        self.assertTrue(java.exists())
        self.assertEqual(self.commands()[0],['cmake','-E','remove',str(installed)])

    def cubrid_fixture(self):
        path = Path(self.env['CUBRID'])/'bin/cubrid'
        path.parent.mkdir(exist_ok=True)
        path.write_text(f'#!{sys.executable}\n' + '''import json,os,sys
args=sys.argv[1:]
with open(os.environ['COMMAND_LOG'],'a') as stream: stream.write(json.dumps(['cubrid',*args])+'\\n')
if args == ['server','status']: print('Server testdb\\nHA-Server extra\\nother irrelevant')
if args == ['service','stop']: sys.exit(3)
''')
        path.chmod(0o755)

    def test_stop_order_registered_servers_and_no_restart(self):
        self.cubrid_fixture()
        for cli in (BASH,PYTHON):
            with self.subTest(cli=cli):
                self.log.unlink(missing_ok=True)
                result = self.invoke('stop-and-build',cli=cli)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(self.commands(),[
                    ['cmake','--build','--preset','debug'],['cubrid','server','status'],
                    ['cubrid','server','stop','testdb'],['cubrid','server','stop','extra'],
                    ['cubrid','service','stop'],['cubrid','broker','stop'],
                    ['cmake','--install',self.env['CUBRID_BUILD_DIR'],'--prefix',self.env['CUBRID']]])

    def test_compile_failure_prevents_stop_and_install_failure_propagates(self):
        self.cubrid_fixture()
        self.env['COMPILE_STATUS']='9'
        result = self.invoke('stop-and-build')
        self.assertEqual(result.returncode,9,result.stderr)
        self.assertEqual(self.commands(),[['cmake','--build','--preset','debug']])
        self.env['INSTALL_STATUS']='12'
        result = self.invoke('install','0')
        self.assertEqual(result.returncode,12,result.stderr)
        self.assertNotIn('completed successfully',result.stdout)

    def test_stop_timeout_preserves_native_process_and_prevents_install(self):
        executable=Path(self.env['CUBRID'])/'native-sleep'
        shutil.copy2(shutil.which('sleep'),executable)
        native=subprocess.Popen([str(executable),'60'])
        child=subprocess.Popen([str(PYTHON),'stop-and-build'],cwd=self.root,env=self.env,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            stdout,stderr=child.communicate(timeout=38)
            self.assertEqual(child.returncode,1,stderr)
            self.assertIn('did not finish within 30 seconds',stderr)
            self.assertIsNone(native.poll())
            self.assertEqual(self.commands(),[['cmake','--build','--preset','debug']])
        finally:
            native.terminate()
            native.wait(timeout=5)
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=5)

    def test_delete_guards_and_marker_preservation(self):
        for cli in (BASH,PYTHON):
            for guarded in (str(self.root),self.env['CUBRID_BUILD_DIR'],self.env['HOME']):
                with self.subTest(cli=cli,guarded=guarded):
                    env=dict(self.env,CUBRID=guarded)
                    result=self.invoke('installation-delete','0',cli=cli,env=env)
                    self.assertEqual(result.returncode,64,result.stderr)
                    self.assertTrue(Path(guarded).exists())
            for name in ('db_vinf','db_lgat'):
                marker=Path(self.env['CUBRID'])/name
                marker.touch()
                result=self.invoke('installation-delete','0',cli=cli)
                self.assertEqual(result.returncode,1,result.stderr)
                self.assertTrue(marker.exists())
                marker.unlink()

    def test_canonical_delete_guard_rejects_build_alias(self):
        prefix=Path(self.env['CUBRID'])
        alias=self.root/'build-alias'
        alias.symlink_to(prefix)
        self.env['CUBRID_BUILD_DIR']=str(alias/'not-yet'/'..'/'build')
        result=self.invoke('installation-delete','0')
        self.assertEqual(result.returncode,64,result.stderr)
        self.assertTrue(prefix.exists())


if __name__ == '__main__':
    unittest.main()
