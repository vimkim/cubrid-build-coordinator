"""Command parsing and documented exit statuses."""
import os
import shutil
import subprocess
import sys

from .actions import parse
from .errors import ActionError, UsageError, command_text


USAGE = '''Usage: cubrid-build-coordinator ACTION [ARGUMENTS]

Actions:
  configure
  build
  target <target>
  build-and-install [runtime-wait-seconds] [--stop-runtime]
  install [runtime-wait-seconds]
  install-target <target> [runtime-wait-seconds]
  runtime [lock-wait-seconds] [--database NAME] -- command [args...]
  installation-use [lock-wait-seconds] [--database NAME] -- command [args...]
  installation-delete [lock-wait-seconds]
  locks [--all] [--json]

configure generates build rules; build produces artifacts; install copies existing artifacts.
build-and-install combines build and install. --stop-runtime explicitly stops services before installation.

Coordinator lock contention returns 75. Resolve the busy state before retrying.
Child command failures retain their exit status. Read stderr for the failure reason.
'''


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    action = args[0] if args else 'command'
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
        request = parse(action, args, os.environ)
        from .coordinator import Coordinator
        for command in ('cmake', 'realpath'):
            if not shutil.which(command):
                raise UsageError(f'Required executable {command!r} was not found on PATH. '
                                 'Select an environment that provides it before retrying.')
        coordinator = Coordinator()
        coordinator.execute(request)
        return 0
    except UsageError as error:
        print(f'Error: {action} failed (exit {error.status}): {error}\n{USAGE}', file=sys.stderr, end='')
        return error.status
    except ActionError as error:
        print(f'Error: {action} failed (exit {error.status}): '
              f'{str(error) or "Operation failed. Inspect the diagnostics above."}', file=sys.stderr)
        return error.status
    except subprocess.CalledProcessError as error:
        status = error.returncode if error.returncode >= 0 else 128 - error.returncode
        print(f'Error: {action} failed (exit {status}): {command_text(error.cmd)}', file=sys.stderr)
        return status
    except OSError as error:
        print(f'Error: {action} failed (exit 1): {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f'Error: {action} interrupted (exit 130).', file=sys.stderr)
        return 130
