"""Parse the complete action before acquiring locks or starting commands."""
from dataclasses import dataclass

from .errors import UsageError
from .locks import timeout_seconds
from .presets import verify_destdir


def seconds(value, source):
    try:
        return timeout_seconds(value)
    except UsageError as error:
        raise UsageError(f'{source}: {error}') from error


@dataclass(frozen=True)
class Action:
    name: str
    arguments: tuple[str, ...]
    build_wait: int = 0
    wait: int = 0
    target: str = ''
    database: str = ''
    command: tuple[str, ...] = ()


def parse(action, arguments, env):
    args = list(arguments)
    target = database = ''
    command = ()
    wait = 0
    runtime_default = env.get('CUBRID_RUNTIME_LOCK_TIMEOUT') or '300'
    if action in ('configure', 'compile', 'stop-and-build'):
        if args:
            raise UsageError(f'{action} takes no arguments')
        if action == 'stop-and-build':
            wait = seconds(runtime_default, 'CUBRID_RUNTIME_LOCK_TIMEOUT')
    elif action in ('target', 'install-target'):
        maximum = 1 if action == 'target' else 2
        if not 1 <= len(args) <= maximum or not args[0].strip():
            raise UsageError(f'{action} requires a non-empty CMake target'
                             + (' and optional timeout' if maximum == 2 else ''))
        target = args[0]
        wait = seconds(args[1] if len(args) == 2 else '0', f'{action} timeout')
    elif action in ('build', 'install', 'installation-delete'):
        if len(args) > 1:
            raise UsageError(f'{action} accepts at most one timeout')
        default = runtime_default if action == 'installation-delete' else '0'
        source = ('CUBRID_RUNTIME_LOCK_TIMEOUT' if not args and action == 'installation-delete'
                  else f'{action} timeout')
        wait = seconds(args[0] if args else default, source)
    elif action in ('runtime', 'installation-use'):
        explicit_wait = bool(args and args[0] not in ('--', '--database'))
        wait = seconds(args.pop(0) if explicit_wait else runtime_default,
                       f'{action} timeout' if explicit_wait else 'CUBRID_RUNTIME_LOCK_TIMEOUT')
        if args and args[0] == '--database':
            if len(args) < 2 or not args[1].strip() or args[1].startswith('--'):
                raise UsageError(f'{action} --database requires a non-empty database name')
            _, database = args[:2]
            args = args[2:]
        if args and args[0].startswith('--') and args[0] != '--':
            raise UsageError(f'{action} has an unexpected option: {args[0]}. '
                             'Place command arguments after --.')
        if args and args[0] == '--':
            args = args[1:]
        if not args or not args[0]:
            raise UsageError(f'{action} requires a command after --')
        command = tuple(args)
    else:
        raise UsageError(f'unknown action: {action}')
    build_wait = (0 if action in ('runtime', 'installation-use') else
                  seconds(env.get('CUBRID_BUILD_LOCK_TIMEOUT') or '600', 'CUBRID_BUILD_LOCK_TIMEOUT'))
    if action in ('build', 'install', 'target', 'install-target', 'stop-and-build'):
        verify_destdir(env)
    return Action(action, tuple(arguments), build_wait, wait, target, database, command)
