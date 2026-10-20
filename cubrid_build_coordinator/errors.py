"""Action failures with the exit status expected by existing callers."""


class ActionError(Exception):
    def __init__(self, message: str = "", status: int = 1):
        super().__init__(message)
        self.status = status


class UsageError(ActionError):
    def __init__(self, message: str):
        super().__init__(message, 64)
