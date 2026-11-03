"""The only subprocess boundary. No shell, writes, retries or fallback backend."""

import json
import math
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .errors import (
    ZoteroCliNotFoundError,
    ZoteroCommandError,
    ZoteroParseError,
    ZoteroTimeoutError,
)


def discover_executable(
    override: str | Path | None = None, *, environ: Mapping[str, str] | None = None
) -> Path:
    """Explicit path > ZOTERO_CLI_PATH > PATH. Never guess a user's install location."""
    env = os.environ if environ is None else environ
    explicit = override if override is not None else env.get("ZOTERO_CLI_PATH")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            raise ZoteroCliNotFoundError("ZOTERO_CLI_PATH must be an absolute executable path.")
    else:
        found = shutil.which("zotero-cli", path=env.get("PATH", "")) or shutil.which(
            "zotero-cli.exe", path=env.get("PATH", "")
        )
        if not found:
            raise ZoteroCliNotFoundError(
                "zotero-cli was not found on this Python process's PATH. "
                "Restart the terminal after installation or set ZOTERO_CLI_PATH."
            )
        path = Path(found)
    path = path.resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ZoteroCliNotFoundError(f"CLI executable does not exist or is not executable: {path}")
    if path.suffix.lower() in {".cmd", ".bat"} or (
        os.name == "nt" and path.suffix.lower() != ".exe"
    ):
        raise ZoteroCliNotFoundError(
            "Use a native CLI executable, not a shell script/command file."
        )
    return path


def _diagnostic_text(value: str | bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


class CommandRunner:
    """Internal adapter implementation; consumers should use ZoteroClient."""

    def __init__(
        self,
        cli_path: str | Path | None = None,
        *,
        timeout: float = 30,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self.cli_path = cli_path
        self.timeout = timeout
        self.last_stderr = ""
        self.environ = dict(os.environ if environ is None else environ)
        # Empty key values also prevent upstream config defaults from supplying write credentials.
        self.environ.update(
            PYTHONUTF8="1",
            PYTHONIOENCODING="utf-8",
            ZOTERO_LOCAL="true",
            ZOTERO_BACKEND="api",
            ZOTERO_SEARCH_BACKEND="api",
            ZOTERO_NO_CLAUDE="true",
            ZOTERO_API_KEY="",
            ZOTERO_LOCAL_API_KEY="",
            ZOTERO_LOCAL_WRITE="false",
            ZOTERO_LIBRARY_TYPE="user",
            ZOTERO_LIBRARY_ID="0",
        )

    def run_json(self, arguments: Sequence[str]) -> Any:
        self.last_stderr = ""
        args = list(arguments)
        permitted = (
            args == ["config"]
            or args[:1] == ["search"]
            or args[:2] in (["get", "metadata"], ["get", "fulltext"])
            or args[:2] == ["annotations", "list"]
            or args[:1] == ["read"]
        )
        if not permitted:
            raise ValueError("Only the adapter's read-only CLI operations are permitted")
        executable = discover_executable(self.cli_path, environ=self.environ)
        operation = " ".join(args[:2]) if args[0] in {"get", "annotations"} else args[0]
        try:
            result = subprocess.run(
                [str(executable), "--json", *args],
                shell=False,
                capture_output=True,
                encoding="utf-8",
                errors="strict",
                timeout=self.timeout,
                env=self.environ,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            self.last_stderr = _diagnostic_text(exc.stderr)
            raise ZoteroTimeoutError(
                f"CLI {operation} exceeded {self.timeout:g} seconds.",
                stdout=_diagnostic_text(exc.stdout),
                stderr=self.last_stderr,
            ) from exc
        except UnicodeDecodeError as exc:
            raise ZoteroParseError(f"CLI {operation} returned invalid UTF-8.") from exc
        except OSError as exc:
            raise ZoteroCommandError(f"Cannot launch CLI {operation}: {exc}") from exc

        self.last_stderr = result.stderr
        diagnostics = dict(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            if result.returncode:
                raise ZoteroCommandError(
                    f"CLI {operation} exited {result.returncode}: {result.stderr.strip()[:1000]}",
                    **diagnostics,
                ) from exc
            raise ZoteroParseError(f"CLI {operation} did not return JSON.", **diagnostics) from exc
        if not isinstance(envelope, dict):
            raise ZoteroParseError("Expected a CLI JSON envelope object.", **diagnostics)
        if result.returncode or envelope.get("ok") is False:
            error = envelope.get("error")
            error = error if isinstance(error, dict) else {}
            message = str(
                error.get("message") or result.stderr.strip() or "Unspecified CLI failure"
            )
            raise ZoteroCommandError(
                f"CLI {operation} failed: {message[:1000]}",
                code=error.get("code"),
                **diagnostics,
            )
        if (
            envelope.get("ok") is not True
            or type(envelope.get("schema")) is not int
            or envelope.get("schema") != 1
            or "data" not in envelope
        ):
            raise ZoteroParseError("Unsupported or incomplete CLI JSON envelope.", **diagnostics)
        if envelope.get("command") != operation:
            raise ZoteroParseError(
                "CLI response command does not match the request.", **diagnostics
            )
        return envelope["data"]
