"""Tests for the Pydantic config models and generators."""

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from harness.generators.agents_md import render_agents_md
from harness.generators.basic import build_autumn, build_bsl_ls, build_compose, render_packagedef
from harness.generators.mcp import build_mcp
from harness.models import EnvFile, McpServer, VSCodeSettings
from harness.models.env import replace_env_value


def test_autumn_has_required_keys() -> None:
    data = json.loads(build_autumn().to_json())
    vrunner = data["vrunner"]
    assert vrunner["ibcmd"] is True
    assert vrunner["src"] == "./src/cf"
    assert vrunner["ibconnection"] == "/F./build/ib"


def test_autumn_ibcmd_can_be_disabled() -> None:
    data = json.loads(build_autumn(use_ibcmd=False).to_json())
    assert data["vrunner"]["ibcmd"] is False


def test_autumn_rejects_unknown_field() -> None:
    from harness.models import AutumnProperties

    with pytest.raises(ValidationError):
        AutumnProperties.model_validate({"bogus": 1})


def test_bsl_ls_defaults() -> None:
    data = json.loads(build_bsl_ls().to_json())
    assert data["language"] == "ru"
    assert "$schema" in data


def test_mcp_server_requires_one_transport() -> None:
    with pytest.raises(ValidationError):
        McpServer()
    with pytest.raises(ValidationError):
        McpServer(command="a", url="http://x")


def test_mcp_default_servers_and_no_platform_tools() -> None:
    data = json.loads(build_mcp(ipc_port=40300).to_json())
    servers = data["mcpServers"]
    assert set(servers) == {"vrunner-devops", "bsl-platform-help", "bsl-atlas-rag", "bsl-lsp"}
    assert servers["bsl-atlas-rag"]["url"].endswith("/mcp")


def test_mcp_servers_use_real_cli_flags() -> None:
    cfg = build_mcp(ipc_port=40300)
    help_server = cfg.mcp_servers["bsl-platform-help"]
    assert help_server.args == ["-m", "mcp_bsl_context", "-p", "<PLATFORM_DIR>"]
    assert help_server.env == {"MCP_BSL_SEARCH_DEFAULT_MODE": "keyword"}
    lsp = cfg.mcp_servers["bsl-lsp"]
    assert lsp.args is not None
    assert lsp.args[0] == "mcp"
    assert "--mcp" not in lsp.args


def test_mcp_python_command_is_configurable() -> None:
    cfg = build_mcp(ipc_port=40300, python_command="C:/py/python.exe")
    assert cfg.mcp_servers["bsl-platform-help"].command == "C:/py/python.exe"


def test_mcp_platform_tools_carries_port() -> None:
    cfg = build_mcp(
        ipc_port=40555,
        include_platform_tools=True,
        platform_tools_js=Path("ext/out/src/index.js"),
        ipc_token="tok",
    )
    env = cfg.mcp_servers["mcp-1c-platform-tools"].env
    assert env is not None
    assert env["ONEC_IPC_PORT"] == "40555"
    assert env["ONEC_IPC_TOKEN"] == "tok"


def test_mcp_platform_tools_placeholder_script() -> None:
    cfg = build_mcp(ipc_port=40300, include_platform_tools=True)
    args = cfg.mcp_servers["mcp-1c-platform-tools"].args
    assert args == ["<PLATFORM_TOOLS_JS>"]


def test_mcp_uses_platform_dir_or_placeholder() -> None:
    placeholder = build_mcp(ipc_port=40300).to_json()
    assert "<PLATFORM_DIR>" in placeholder
    real = build_mcp(ipc_port=40300, platform_dir=Path("C:/1cv8/8.3.27")).to_json()
    assert "C:/1cv8/8.3.27" in real


def test_compose_yaml_roundtrip() -> None:
    parsed = yaml.safe_load(build_compose().to_yaml())
    atlas = parsed["services"]["bsl-atlas"]
    assert atlas["image"] == "armankudaibergenov/bsl-atlas:latest"
    assert "${SOURCE_PATH}:/data/source:ro" in atlas["volumes"]
    assert "./data/chroma_db:/data/chroma_db" in atlas["volumes"]
    assert atlas["environment"]["INDEXING_MODE"] == "${INDEXING_MODE:-fast}"
    assert atlas["environment"]["OLLAMA_MODEL"] == "${OLLAMA_MODEL:-qwen3-embedding:8b}"
    assert atlas["environment"]["OLLAMA_BASE_URL"].endswith("host.docker.internal:11434}")
    assert list(parsed["services"]) == ["bsl-atlas"]


def test_env_render_sorted_and_posix() -> None:
    env = EnvFile(source_path=Path("C:/bsl-src/abc"), onec_ipc_port=40241)
    assert env.render() == ("INDEXING_MODE=fast\nONEC_IPC_PORT=40241\nSOURCE_PATH=C:/bsl-src/abc\n")


def test_env_port_range_validated() -> None:
    with pytest.raises(ValidationError):
        EnvFile(source_path=Path("x"), onec_ipc_port=80)


def test_replace_env_value_preserves_other_lines() -> None:
    out = replace_env_value("A=1\nSOURCE_PATH=old\n", "SOURCE_PATH", "new")
    assert out == "A=1\nSOURCE_PATH=new\n"
    assert replace_env_value("A=1\n", "B", "2") == "A=1\nB=2\n"


def test_vscode_settings_preserve_user_keys() -> None:
    s = VSCodeSettings.from_text('{"editor.tabSize": 2}').with_ipc_port(40300)
    data = json.loads(s.to_json())
    assert data["editor.tabSize"] == 2
    assert s.ipc_port() == 40300
    assert VSCodeSettings.from_text("").ipc_port() is None


def test_packagedef_sanitizes_name() -> None:
    assert 'Имя("my-proj")' in render_packagedef("my proj")


def test_agents_md_has_five_phases_and_gates() -> None:
    text = render_agents_md()
    for n in range(1, 6):
        assert f"## Phase {n}:" in text
    assert "&НаСервере" in text
    assert "--mode ExtendedModulesCheck" in text
    assert "-ExtendedModulesCheck" not in text
    assert "Создаётся временная информационная база" in text
    assert "--settings ./autumn-properties.json" in text
    assert "Ошибка чтения параметров команды" in text
    assert "vrunner 3.0.2 mis-parses" not in text
    assert "CLI Fallbacks" in text
    for script in (
        "1c-config-index/scripts/config-index.py",
        "1c-meta-compile/scripts/meta-compile.py",
        "1c-meta-validate/scripts/meta-validate.py",
        "1c-cf-validate/scripts/cf-validate.py",
        "1c-bsl-validate/scripts/bsl-validate.py",
    ):
        assert script in text
