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
    stop_runtime: bool = False


def parse(action, arguments, env):
    args = list(arguments)
    target = database = ''
    command = ()
    wait = 0
    stop_runtime = False
    runtime_default = env.get('CUBRID_RUNTIME_LOCK_TIMEOUT') or '300'
    if action in ('compile', 'stop-and-build'):
        replacement = 'build' if action == 'compile' else 'build-and-install --stop-runtime'
        raise UsageError(f'{action} was renamed; use {replacement}')
    if action in ('configure', 'build'):
        if args:
            raise UsageError(f'{action} takes no arguments')
    elif action in ('target', 'install-target'):
        maximum = 1 if action == 'target' else 2
        if not 1 <= len(args) <= maximum or not args[0].strip():
            raise UsageError(f'{action} requires a non-empty CMake target'
                             + (' and optional timeout' if maximum == 2 else ''))
        target = args[0]
        wait = seconds(args[1] if len(args) == 2 else '0', f'{action} timeout')
    elif action in ('build-and-install', 'install', 'installation-delete'):
        if action == 'build-and-install' and '--stop-runtime' in args:
            args.remove('--stop-runtime')
            stop_runtime = True
        if len(args) > 1:
            raise UsageError(f'{action} accepts at most one timeout')
        runtime_wait = action == 'installation-delete' or stop_runtime
        default = runtime_default if runtime_wait else '0'
        source = ('CUBRID_RUNTIME_LOCK_TIMEOUT' if not args and runtime_wait
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
    if action in ('build-and-install', 'install', 'target', 'install-target'):
        verify_destdir(env)
    return Action(action, tuple(arguments), build_wait, wait, target, database, command, stop_runtime)
