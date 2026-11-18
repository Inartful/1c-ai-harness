"""Tests for `harness query`."""

import base64
from pathlib import Path

import pytest

from fakes import FakeRunner
from harness.errors import ConfigError
from harness.services.query import encode_config, encoded_script, run_query


def test_encoded_script_decodes_utf16le_and_reads_stdin() -> None:
    script = base64.b64decode(encoded_script()).decode("utf-16-le")
    assert "V83.COMConnector" in script
    assert "ConvertFrom-Json" in script
    assert "[Console]::In.ReadToEnd()" in script


def test_encode_config_is_ascii_and_round_trips() -> None:
    encoded = encode_config(query="ВЫБРАТЬ 1 КАК К", server="1c-srv", ref="db", pwd="secret")
    assert encoded.isascii()
    assert '"pwd": "secret"' in base64.b64decode(encoded).decode("utf-8")


def test_password_never_in_argv() -> None:
    runner = FakeRunner(stdout='{"columns":[],"rows":[]}')
    run_query("ВЫБРАТЬ 1", server="s", ref="r", usr="u", pwd="topsecret", ssh="h", runner=runner)
    argv_text = " ".join(runner.calls[0])
    sent = runner.inputs[0] or ""
    assert "topsecret" not in argv_text
    assert base64.b64encode(b"topsecret").decode() not in argv_text
    assert "topsecret" in base64.b64decode(sent.strip()).decode("utf-8")


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
