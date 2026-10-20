"""Adapt cub-workenv's Bash interface without interpreting its state files."""
import os
from pathlib import Path
import tempfile

from .errors import ActionError
from .locks import canonical
from .processes import checked


SELECT = r'''
load_environment() {
local shell_code status=0
shell_code="$("$1" env --worktree "$2" --preset "$3" --install "$4")" || status=$?
eval "$shell_code" || status=$?
[[ "$status" == 0 ]] || return "$status"
if [[ "${CUBRID_RUNTIME_READY:-0}" != 1 ]]; then
    printf 'Initialize this worktree explicitly with cub-workenv init before host DB use.\n' >&2
    return 1
fi
}
load_environment "$@" || exit "$?"
/usr/bin/env -0 >&"$5"
'''


def select(env, install):
    # A separate descriptor keeps output from sourced shell code out of the
    # environment protocol. Stream command diagnostics normally and preserve
    # the original selector failure even when its cleanup code succeeds.
    with tempfile.TemporaryFile() as output:
        checked(['/bin/bash', '--noprofile', '--norc', '-c', SELECT, 'cubrid-workenv-adapter',
                 env.get('CUB_WORKENV_CLI') or 'cub-workenv', env.get('PWD') or os.getcwd(),
                 env['PRESET_MODE'], env['CUBRID'], str(output.fileno())],
                env=env, pass_fds=(output.fileno(),))
        output.seek(0)
        selected = dict(os.fsdecode(entry).split('=', 1)
                        for entry in output.read().split(b'\0') if entry)
    if selected.get('CUBRID_RUNTIME_READY') != '1':
        raise ActionError('Initialize this worktree explicitly with cub-workenv init before host DB use.')
    if not selected.get('CUBRID') or canonical(selected['CUBRID']) != install:
        raise ActionError('Selected installation does not match the locked installation.')
    return selected


def require_database(env, name):
    directory = env.get('CUBRID_DATABASES')
    if directory:
        try:
            with (Path(directory) / 'databases.txt').open(errors='surrogateescape') as registry:
                if any(fields and fields[0] == name for line in registry if (fields := line.split())):
                    return
        except OSError:
            pass
    raise ActionError(f'Database {name} is not registered; use cub-workenv create-db explicitly.')
