"""Command parsing and documented exit statuses."""
import shutil
import subprocess
import sys

from .errors import ActionError, UsageError


USAGE = '''Usage: cubrid-build-coordinator ACTION [ARGUMENTS]

Actions:
  configure
  compile
  target <target>
  build [runtime-wait-seconds]
  install [runtime-wait-seconds]
  install-target <target> [runtime-wait-seconds]
  runtime [lock-wait-seconds] [--database NAME] -- command [args...]
  installation-use [lock-wait-seconds] [--database NAME] -- command [args...]
  installation-delete [lock-wait-seconds]
  stop-and-build
  locks [--all] [--json]

Exit status 75 means that the operation is safe to retry later.
'''


def validate(action, args):
    if action in ('configure', 'compile', 'stop-and-build'):
        if args:
            raise UsageError(f'{action} takes no arguments')
    elif action == 'target':
        if len(args) != 1:
            raise UsageError('target requires exactly one CMake target')
    elif action in ('build', 'install', 'installation-delete'):
        if len(args) > 1:
            raise UsageError(f'{action} accepts at most one timeout')
    elif action == 'install-target':
        if not 1 <= len(args) <= 2:
            raise UsageError('install-target requires a target and optional timeout')
    elif action not in ('runtime', 'installation-use'):
        raise UsageError(f'unknown action: {action}')


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args == ['--help'] or args == ['-h']:
            print(USAGE, end='')
            return 0
        if not args:
            raise UsageError('action is required')
        action = args.pop(0)
        if action == 'locks':
            from .status import main as inspect
            return inspect(args)
        from .coordinator import Coordinator
        coordinator = Coordinator()
        for command in ('cmake', 'realpath'):
            if not shutil.which(command):
                raise UsageError(f'{command} is not available')
        validate(action, args)
        coordinator.execute(action, args)
        return 0
    except UsageError as error:
        print(f'Error: {error}\n{USAGE}', file=sys.stderr, end='')
        return error.status
    except ActionError as error:
        if str(error):
            print(str(error), file=sys.stderr)
        return error.status
    except subprocess.CalledProcessError as error:
        return error.returncode if error.returncode >= 0 else 128 - error.returncode
    except OSError as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
