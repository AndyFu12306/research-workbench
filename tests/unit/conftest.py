from unittest.mock import Mock

import pytest

from research_workbench.integrations.zotero import client, runner


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        Mock(side_effect=AssertionError("Unmocked subprocess in unit test")),
    )
    monkeypatch.setattr(
        client, "build_opener", Mock(side_effect=AssertionError("Unmocked network in unit test"))
    )


@pytest.fixture
def executable(tmp_path):
    path = tmp_path / "tool directory" / "zotero-cli.exe"
    path.parent.mkdir()
    path.write_bytes(b"unit-test-placeholder")
    path.chmod(0o755)
    return path.resolve()
