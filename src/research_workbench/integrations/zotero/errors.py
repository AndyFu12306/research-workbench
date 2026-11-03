"""Public errors, independent of subprocess and upstream Python packages."""


class ZoteroError(Exception):
    """Base error for callers; never log raw diagnostics without reviewing them."""

    layer = "adapter"


class ZoteroCliNotFoundError(ZoteroError):
    layer = "executable"


class ZoteroUnavailableError(ZoteroError):
    layer = "local_api"


class ZoteroCommandError(ZoteroError):
    """Raw diagnostics are retained for debugging, not automatically logged."""

    layer = "cli"

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.code = code


class ZoteroTimeoutError(ZoteroCommandError):
    """One CLI process exceeded the configured finite timeout."""


class ZoteroParseError(ZoteroCommandError):
    layer = "cli_output"
