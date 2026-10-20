"""Action failures with the exit status expected by existing callers."""
import re
import shlex
from pathlib import Path


def redact(command):
    result = []
    hide_next = False
    sensitive = re.compile(r'(^|[-_])(password|passwd|token|secret|api[-_]?key|access[-_]?key)$', re.I)
    for argument in command:
        argument = str(argument)
        key, separator, _ = argument.partition('=')
        if hide_next:
            result.append('<redacted>')
            hide_next = False
        elif sensitive.search(key) or key == '-p':
            result.append(key + '=<redacted>' if separator else argument)
            hide_next = not separator
        else:
            result.append(argument)
    return result


def command_text(command):
    arguments = redact(command)
    program = Path(arguments[0]).name if arguments else ''
    shells = ('bash', 'sh', 'dash', 'zsh', 'ksh')
    for index, argument in enumerate(arguments[1:], 1):
        if program.startswith('python'):
            match = re.fullmatch(r'(-[bBdEiIOPqRsSuv]*c)(.*)', argument, re.S)
            if not match:
                continue
            if match[2]:
                arguments[index] = match[1] + '<inline code>'
                break
        elif program in (*shells, 'csql'):
            if argument not in ('-c', '--command') and not (
                    program in shells and re.fullmatch(r'-[a-zA-Z]*c[a-zA-Z]*', argument)):
                continue
        else:
            break
        if index + 1 < len(arguments):
            arguments[index + 1] = '<inline code>'
        break
    return shlex.join(arguments)


class ActionError(Exception):
    def __init__(self, message: str = "", status: int = 1):
        super().__init__(message)
        self.status = status


class UsageError(ActionError):
    def __init__(self, message: str):
        super().__init__(message, 64)
