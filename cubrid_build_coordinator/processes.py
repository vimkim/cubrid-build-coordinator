"""Foreground supervision and observation of installed native executables."""
import os
from pathlib import Path
import signal
import subprocess

from .errors import ActionError, command_text


def run(command, *, env=None, stdout=None, pass_fds=()):
    """Forward signals to the immediate child, then wait through its cleanup.

    The caller retains its locks until this function returns. Children inherit
    the terminal and streams, but never the caller's lock descriptors.
    """
    child = None
    pending = []

    def forward(signum, _frame):
        if child is None:
            pending.append(signum)
        else:
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

    # Installing handlers also resets ignored INT/QUIT in exec'd children.
    forwarded = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)
    previous = {sig: signal.signal(sig, forward) for sig in forwarded}
    try:
        try:
            child = subprocess.Popen(command, env=env, stdout=stdout,
                                     close_fds=True, pass_fds=pass_fds)
        except (FileNotFoundError, PermissionError) as error:
            # The old Python exec trampoline reports launch failures as 1.
            raise ActionError(f'Could not start command: {command_text(command)}\n{error}') from error
        for sig in pending:
            forward(sig, None)
        status = child.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if status < 0:
        try:
            name = signal.Signals(-status).name
        except ValueError:
            name = f'signal {-status}'
        raise ActionError(f'Command terminated by {name} (exit {128 - status}): '
                          f'{command_text(command)}', 128 - status)
    return status


def checked(command, *, env=None, stdout=None, pass_fds=()):
    status = run(command, env=env, stdout=stdout, pass_fds=pass_fds)
    if status:
        raise ActionError(f'Command failed with exit status {status}: {command_text(command)}\n'
                          'Read the command output above for details.', status)


def active_install_processes(prefix):
    """Observe native executable paths, including unlinked executables.

    This is not a reservation against direct launches or an inventory of mapped
    libraries. Unreadable processes are outside the observable set.
    """
    result = []
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            executable = os.readlink(process / 'exe').removesuffix(' (deleted)')
            if not executable.startswith(prefix.rstrip('/') + '/'):
                continue
            try:
                name = (process / 'comm').read_text().strip()
            except OSError:
                name = 'unknown'
            result.append((int(process.name), name, executable))
        except OSError:
            continue
    return sorted(result)
