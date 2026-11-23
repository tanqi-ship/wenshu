"""L1 独立稳定错误，不复用编译错误。"""


class ExtractionError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "", fragments: tuple[str, ...] = ()):
        self.code = code
        self.message = message
        self.path = path
        self.fragments = fragments
        super().__init__(f"{code} ({path}): {message}")
