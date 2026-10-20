"""Translate current action names only when exercising the frozen Bash baseline."""
from pathlib import Path


BASELINE = Path(__file__).parent / 'fixtures/bash/cubrid-build-coordinator.sh'


def coordinator_command(cli, *arguments):
    args = list(arguments)
    if Path(cli) == BASELINE and args:
        if args[0] == 'build':
            args[0] = 'compile'
        elif args[0] == 'build-and-install':
            if '--stop-runtime' in args:
                args = ['stop-and-build']
            else:
                args[0] = 'build'
    return [str(cli), *map(str, args)]
