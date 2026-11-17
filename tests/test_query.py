"""Tests for `harness query`."""

import base64
from pathlib import Path

import pytest

from fakes import FakeRunner
from harness.errors import ConfigError
from harness.services.query import build_encoded_command, run_query


def test_build_encoded_command_decodes_utf16le() -> None:
    b64 = build_encoded_command(
        query="ВЫБРАТЬ 1 КАК К",
        server="1c-srv",
        ref="my_db",
        usr="Admin",
        pwd="secret",
    )
    script = base64.b64decode(b64).decode("utf-16-le")
    assert "V83.COMConnector" in script
    assert "ConvertFrom-Json" in script


def test_run_query_local_and_single_row_normalization() -> None:
    runner = FakeRunner(stdout='{"columns":"Ссылка","rows":{"Ссылка":"Док1"}}')
    res = run_query(
        "ВЫБРАТЬ 1",
        server="1c-srv",
        ref="my_db",
        ssh="local",
        runner=runner,
    )
    assert runner.calls[0][0] == "powershell"
    assert res.columns == ["Ссылка"]
    assert res.rows == [{"Ссылка": "Док1"}]


def test_run_query_over_ssh() -> None:
    runner = FakeRunner(stdout='\ufeff{"columns":["N"],"rows":[{"N":1},{"N":2}]}')
    res = run_query(
        "ВЫБРАТЬ 1",
        server="1c-srv",
        ref="my_db",
        ssh="user@10.0.0.5",
        runner=runner,
    )
    assert runner.calls[0][0] == "ssh"
    assert "user@10.0.0.5" in runner.calls[0]
    assert "powershell" in runner.calls[0]
    assert res.rows == [{"N": 1}, {"N": 2}]


def test_run_query_validation_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="provide either --file or both"):
        run_query("ВЫБРАТЬ 1", server="1c-srv", runner=FakeRunner())
    with pytest.raises(ConfigError, match="must not be empty"):
        run_query("   ", file_path=tmp_path, runner=FakeRunner())
    with pytest.raises(ConfigError, match="invalid JSON"):
        run_query("ВЫБРАТЬ 1", server="s", ref="r", runner=FakeRunner(stdout="not-json"))
