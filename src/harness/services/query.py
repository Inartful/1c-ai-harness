"""Implementation of `harness query`: execute 1C queries via V83.COMConnector (local or SSH)."""

import base64
import json
import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from harness.errors import ConfigError
from harness.system.runner import CommandRunner, run

_QUERY_TIMEOUT_S = 300.0

_PS_TEMPLATE = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"
$rawCfg = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("__CFG_B64__"))
$cfg = $rawCfg | ConvertFrom-Json
$parts = @()
$qchar = [char]34
if ($cfg.file) {
    $parts += "File=${qchar}$($cfg.file)${qchar}"
} else {
    $parts += "Srvr=${qchar}$($cfg.server)${qchar}"
    $parts += "Ref=${qchar}$($cfg.ref)${qchar}"
}
if ($cfg.usr) { $parts += "Usr=${qchar}$($cfg.usr)${qchar}" }
if ($cfg.pwd) { $parts += "Pwd=${qchar}$($cfg.pwd)${qchar}" }
$connStr = ($parts -join ";") + ";"

$com = New-Object -ComObject V83.COMConnector
$conn = $com.Connect($connStr)
$q = $conn.NewObject("Запрос")
$q.Text = $cfg.query
$res = $q.Execute()
$tt = $res.Выгрузить()
$gp = [Reflection.BindingFlags]::GetProperty
$im = [Reflection.BindingFlags]::InvokeMethod
$colsObj = $tt.GetType().InvokeMember("Колонки", $gp, $null, $tt, $null)
$colCount = $colsObj.GetType().InvokeMember("Количество", $im, $null, $colsObj, $null)
$cols = @()
for ($i = 0; $i -lt $colCount; $i++) {
    $cObj = $colsObj.GetType().InvokeMember("Получить", $im, $null, $colsObj, @($i))
    $cols += [string]$cObj.GetType().InvokeMember("Имя", $gp, $null, $cObj, $null)
}
$rows = @()
foreach ($r in $tt) {
    $obj = [ordered]@{}
    for ($i = 0; $i -lt $colCount; $i++) {
        $col = $cols[$i]
        $val = $r.GetType().InvokeMember("Получить", $im, $null, $r, @($i))
        if ($null -eq $val) {
            $obj[$col] = $null
        } elseif ($val -is [bool] -or $val -is [int] -or $val -is [long] -or
                  $val -is [double] -or $val -is [decimal] -or $val -is [string]) {
            $obj[$col] = $val
        } elseif ($val -is [datetime]) {
            $obj[$col] = $val.ToString("s")
        } else {
            $obj[$col] = $conn.String($val)
        }
    }
    $rows += [pscustomobject]$obj
}
[pscustomobject]@{ columns = @($cols); rows = @($rows) } | ConvertTo-Json -Depth 5 -Compress
"""


class QueryResult(BaseModel):
    """Structured result of `harness query`."""

    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, object]] = Field(default_factory=list)
    seconds: float = 0.0


def build_encoded_command(
    *,
    query: str,
    server: str | None = None,
    ref: str | None = None,
    file_path: Path | None = None,
    usr: str | None = None,
    pwd: str | None = None,
) -> str:
    """Build a UTF-16LE Base64 PowerShell `-EncodedCommand` payload.

    Args:
        query: 1C query language text.
        server: 1C cluster server name (`Srvr`).
        ref: Infobase name on the cluster (`Ref`).
        file_path: File infobase path (`File`), mutually exclusive with server/ref.
        usr: 1C user name (`Usr`).
        pwd: 1C password (`Pwd`).

    Returns:
        Base64-encoded UTF-16LE PowerShell script.
    """
    payload = {
        "server": server or "",
        "ref": ref or "",
        "file": str(file_path) if file_path is not None else "",
        "usr": usr or "",
        "pwd": pwd or "",
        "query": query,
    }
    cfg_b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode(
        "ascii"
    )
    script = _PS_TEMPLATE.replace("__CFG_B64__", cfg_b64)
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _get_env(name: str) -> str | None:
    r"""Read an env var from process environment, falling back to Windows HKCU\Environment."""
    val = os.environ.get(name)
    if val is not None:
        return val
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            raw, _ = winreg.QueryValueEx(key, name)
            return str(raw) if raw else None
    except OSError:
        return None


def run_query(
    query: str,
    *,
    server: str | None = None,
    ref: str | None = None,
    file_path: Path | None = None,
    usr: str | None = None,
    pwd: str | None = None,
    ssh: str | None = None,
    runner: CommandRunner = run,
) -> QueryResult:
    """Execute a 1C query via `V83.COMConnector`, locally or over SSH.

    Args:
        query: 1C query text.
        server: 1C cluster server (`Srvr`).
        ref: Infobase name (`Ref`).
        file_path: File infobase directory (`File`).
        usr: 1C user name (falls back to `ONEC_USR`).
        pwd: 1C password (falls back to `ONEC_PWD`).
        ssh: Optional SSH target (`user@host`, falls back to `ONEC_SSH`).
        runner: Command runner, injectable for tests.

    Returns:
        Parsed columns, rows and elapsed seconds.

    Raises:
        ConfigError: If connection parameters are incomplete or output is not valid JSON.
    """
    if file_path is None and (not server or not ref):
        msg = "provide either --file or both --server and --ref"
        raise ConfigError(msg)
    if not query.strip():
        msg = "query text must not be empty"
        raise ConfigError(msg)

    resolved_usr = usr if usr is not None else _get_env("ONEC_USR")
    resolved_pwd = pwd if pwd is not None else _get_env("ONEC_PWD")
    resolved_ssh = ssh if ssh is not None else _get_env("ONEC_SSH")
    if resolved_ssh in ("", "local", "none"):
        resolved_ssh = None

    encoded = build_encoded_command(
        query=query,
        server=server,
        ref=ref,
        file_path=file_path,
        usr=resolved_usr,
        pwd=resolved_pwd,
    )
    ps_args = ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
    argv = (
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", resolved_ssh, *ps_args]
        if resolved_ssh
        else ps_args
    )
    result = runner(argv, timeout_s=_QUERY_TIMEOUT_S)
    raw = result.stdout.strip()
    if not raw:
        msg = f"empty response from COMConnector; stderr: {result.stderr.strip()}"
        raise ConfigError(msg)
    # Strip possible UTF-8 BOM or leading noise before JSON object
    idx = raw.find("{")
    if idx > 0:
        raw = raw[idx:]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"invalid JSON from COMConnector: {exc}\noutput: {raw[:300]}"
        raise ConfigError(msg) from exc

    raw_cols = data.get("columns", [])
    if isinstance(raw_cols, str):
        columns = [raw_cols]
    elif isinstance(raw_cols, list):
        columns = [str(c) for c in raw_cols]
    else:
        columns = []
    raw_rows = data.get("rows", [])
    # PowerShell ConvertTo-Json emits a single object instead of array when row count == 1
    if isinstance(raw_rows, dict):
        raw_rows = [raw_rows]
    elif not isinstance(raw_rows, list):
        raw_rows = []
    return QueryResult(columns=columns, rows=raw_rows, seconds=round(result.duration_s, 2))
