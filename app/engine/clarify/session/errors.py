"""会话边界的稳定错误。"""


class SessionError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = ""):
        self.code = code
        self.message = message
        self.path = path
        super().__init__(f"{code} ({path}): {message}")


def fail(code: str, message: str, path: str = "") -> None:
    raise SessionError("SESSION_" + code, message, path=path)
