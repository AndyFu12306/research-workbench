import json
import subprocess
from unittest.mock import Mock

import pytest

from research_workbench.integrations.zotero import (
    ZoteroCliNotFoundError,
    ZoteroCommandError,
    ZoteroParseError,
    ZoteroTimeoutError,
    runner,
)


def completed(data=None, *, returncode=0, stderr="", **envelope):
    body = {"ok": True, "command": "config", "schema": 1, "data": data or {}}
    body.update(envelope)
    return subprocess.CompletedProcess([], returncode, json.dumps(body), stderr)


def test_discovery_uses_explicit_override_before_env_and_path(executable, monkeypatch):
    which = Mock(side_effect=AssertionError("PATH must not be consulted"))
    monkeypatch.setattr(runner.shutil, "which", which)
    assert (
        runner.discover_executable(executable, environ={"ZOTERO_CLI_PATH": "invalid"}) == executable
    )
    assert runner.discover_executable(environ={"ZOTERO_CLI_PATH": str(executable)}) == executable


def test_discovery_tries_both_names_using_python_path(executable, monkeypatch):
    which = Mock(side_effect=[None, str(executable)])
    monkeypatch.setattr(runner.shutil, "which", which)
    assert runner.discover_executable(environ={"PATH": "process-path"}) == executable
    assert which.call_args_list[0].args == ("zotero-cli",)
    assert which.call_args_list[1].args == ("zotero-cli.exe",)
    assert which.call_args_list[0].kwargs == {"path": "process-path"}


@pytest.mark.parametrize("override", ["relative.exe", "", None])
def test_discovery_reports_missing_cli(override, monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", Mock(return_value=None))
    with pytest.raises(ZoteroCliNotFoundError):
        runner.discover_executable(override, environ={"PATH": ""})


def test_missing_override_does_not_silently_fall_back(tmp_path):
    with pytest.raises(ZoteroCliNotFoundError, match="does not exist"):
        runner.discover_executable(tmp_path / "missing.exe")


def test_command_files_are_rejected(tmp_path):
    path = tmp_path / "zotero-cli.cmd"
    path.write_text("echo unsafe")
    path.chmod(0o755)
    with pytest.raises(ZoteroCliNotFoundError, match="native"):
        runner.discover_executable(path)


def test_subprocess_is_absolute_utf8_bounded_and_shell_free(executable, monkeypatch):
    mock = Mock(return_value=completed({"settings": {}}))
    monkeypatch.setattr(runner.subprocess, "run", mock)
    command = runner.CommandRunner(
        executable, timeout=7, environ={"PATH": "a", "ZOTERO_API_KEY": "fake"}
    )
    assert command.run_json(["config"]) == {"settings": {}}
    args, kwargs = mock.call_args
    assert args[0] == [str(executable), "--json", "config"]
    assert kwargs["shell"] is False
    assert kwargs["capture_output"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["timeout"] == 7
    assert kwargs["env"]["ZOTERO_API_KEY"] == ""
    assert kwargs["env"]["ZOTERO_LOCAL_API_KEY"] == ""
    assert kwargs["env"]["ZOTERO_LOCAL_WRITE"] == "false"
    assert kwargs["env"]["ZOTERO_BACKEND"] == "api"
    assert kwargs["env"]["ZOTERO_LOCAL"] == "true"


@pytest.mark.parametrize(
    "args", [["edit", "FAKE0001"], ["notes", "create"], ["annotations", "create"]]
)
def test_runner_rejects_writes(executable, args):
    with pytest.raises(ValueError, match="read-only"):
        runner.CommandRunner(executable).run_json(args)


def test_command_failure_keeps_both_streams_and_code(executable, monkeypatch):
    result = completed(
        returncode=1,
        stderr="transport diagnostic",
        ok=False,
        error={"code": "connection_error", "message": "Local API refused connection"},
    )
    monkeypatch.setattr(runner.subprocess, "run", Mock(return_value=result))
    with pytest.raises(ZoteroCommandError, match="Local API refused") as exc:
        runner.CommandRunner(executable).run_json(["config"])
    assert exc.value.returncode == 1
    assert exc.value.stderr == "transport diagnostic"
    assert exc.value.stdout == result.stdout
    assert exc.value.code == "connection_error"


def test_nonzero_even_with_ok_true_is_failure(executable, monkeypatch):
    monkeypatch.setattr(runner.subprocess, "run", Mock(return_value=completed(returncode=2)))
    with pytest.raises(ZoteroCommandError):
        runner.CommandRunner(executable).run_json(["config"])


def test_ok_false_even_with_zero_exit_is_failure(executable, monkeypatch):
    monkeypatch.setattr(runner.subprocess, "run", Mock(return_value=completed(ok=False)))
    with pytest.raises(ZoteroCommandError):
        runner.CommandRunner(executable).run_json(["config"])


@pytest.mark.parametrize(
    "body", ["not json", "[]", '{"ok": true}', '{"ok":true,"schema":2,"data":{}}']
)
def test_invalid_protocol_is_parse_error(executable, monkeypatch, body):
    monkeypatch.setattr(
        runner.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, body, ""))
    )
    with pytest.raises(ZoteroParseError):
        runner.CommandRunner(executable).run_json(["config"])


def test_launcher_failure_is_command_error_not_parse_error(executable, monkeypatch):
    result = subprocess.CompletedProcess(
        [], 1, "", "uv trampoline failed to canonicalize script path"
    )
    monkeypatch.setattr(runner.subprocess, "run", Mock(return_value=result))
    with pytest.raises(ZoteroCommandError, match="canonicalize") as exc:
        runner.CommandRunner(executable).run_json(["config"])
    assert type(exc.value) is ZoteroCommandError


def test_success_stderr_is_preserved_without_automatic_logging(executable, monkeypatch, caplog):
    monkeypatch.setattr(runner.subprocess, "run", Mock(return_value=completed(stderr="warning")))
    command = runner.CommandRunner(executable)
    command.run_json(["config"])
    assert command.last_stderr == "warning"
    assert not caplog.records


@pytest.mark.parametrize("override", [{"schema": True}, {"command": "other"}])
def test_envelope_identity_and_schema_are_checked(executable, monkeypatch, override):
    monkeypatch.setattr(runner.subprocess, "run", Mock(return_value=completed(**override)))
    with pytest.raises(ZoteroParseError):
        runner.CommandRunner(executable).run_json(["config"])


@pytest.mark.parametrize(
    "failure, expected",
    [
        (
            subprocess.TimeoutExpired("cli", 3, output=b"partial", stderr=b"slow"),
            ZoteroTimeoutError,
        ),
        (PermissionError("execution denied"), ZoteroCommandError),
        (UnicodeDecodeError("utf8", b"\xff", 0, 1, "invalid"), ZoteroParseError),
    ],
)
def test_process_error_mapping(executable, monkeypatch, failure, expected):
    monkeypatch.setattr(runner.subprocess, "run", Mock(side_effect=failure))
    with pytest.raises(expected) as exc:
        runner.CommandRunner(executable).run_json(["config"])
    if isinstance(failure, subprocess.TimeoutExpired):
        assert exc.value.stdout == "partial"
        assert exc.value.stderr == "slow"


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_is_rejected(timeout):
    with pytest.raises(ValueError):
        runner.CommandRunner(timeout=timeout)
