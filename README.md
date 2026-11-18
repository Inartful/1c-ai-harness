# 1c-ai-harness

Workspace bootstrapper, config generator, and CLI toolkit for AI-assisted 1C:Enterprise development.
It wires together the existing open-source 1C ecosystem (`vanessa-runner 3.0`, `claude-code-skills-1c`,
`mcp-bsl-platform-help-context`, `bsl-atlas`, `bsl-language-server`, `gitrules`) and provides fast
wrappers around `ibcmd`, `gitrules`, and `V83.COMConnector`.

## Prerequisites

- **Python 3.12+** (recommended: [uv](https://docs.astral.sh/uv/))
- **1C:Enterprise 8.3.20+** (`1cv8`, `ibcmd` for fast `.cf` build/decompile, `.hbk` platform help)
- **[OneScript](https://oscript.io/) 2.0+** with `opm`:
  - `opm install vanessa-runner` (3.x) — required for `doctor` / `vrunner-mcp`
  - `opm install gitrules` — optional, required for `harness rules unpack/pack` (КД 2.x exchange rules)
- **[bsl-language-server](https://github.com/1c-syntax/bsl-language-server)** — optional, required for `harness check`
- **Docker** — optional, required for [`bsl-atlas`](https://github.com/Arman-Kudaibergenov/bsl-atlas) RAG indexing

## Install

```bash
uv pip install -e ".[dev]"
```

## Quick Start

### 1. Configuration workspace (`src/cf`)

```bash
# Bootstrap workspace configs, AGENTS.md, .mcp.json, docker-compose.yml
harness init ./my-1c-project

# Verify tools, platform files, ports, and workspace consistency
harness doctor ./my-1c-project

# Fetch 1C agent skills into .agents/skills (linked to .claude/skills and .cursor/skills)
harness setup-skills ./my-1c-project

# Fast syntax check with BSL Language Server (no 1C license required)
harness check ./my-1c-project --fail-on-error

# Fast load & .cf build via ibcmd (~5x faster than designer pipeline)
harness build ./my-1c-project
```

### 2. КД 2.x exchange rules & live infobase queries

```bash
# Decompile source/target .cf files into readable XML/BSL trees
harness decompile ./Source/Retail.cf ./work/retail
harness decompile ./Source/Target.cf ./work/target

# Unpack КД 2.x rules (.zip or .xml) into editable directories and .bsl handlers
harness rules unpack "./Source/ExchangeRules.zip" ./work/rules

# Pack modified rules back into .zip (preserves auxiliary XMLs from _raw)
harness rules pack ./work/rules "./work/ExchangeRules-patched.zip"

# Run a 1C query against a local or remote (SSH) infobase (Windows COM)
harness query --server "1c-srv" --ref "my_db" --usr "Admin" --pwd "secret" -q "ВЫБРАТЬ ПЕРВЫЕ 5 Ссылка ИЗ Документ.ОтчетОРозничныхПродажах"
```

> **Note (`harness query`):** Windows-only (`V83.COMConnector`). Requires `comcntr.dll` matching
> PowerShell bitness (usually 64-bit) to be registered (`regsvr32 <platform_bin>\comcntr.dll`).
> Credentials and jump host can be set via `ONEC_SSH`, `ONEC_USR`, and `ONEC_PWD` (process env or
> Windows `HKCU\Environment`); pass `--ssh local` to bypass `ONEC_SSH`. The query text and
> credentials reach PowerShell via stdin, never via the command line.

## Commands

### Workspace commands

| Command | Purpose |
|---|---|
| `harness init <dir>` | Create the workspace tree and generate `packagedef`, `autumn-properties.json`, `.bsl-language-server.json`, `.mcp.json`, `.cursor/mcp.json`, `.vscode/settings.json` (IPC port), `docker-compose.yml`, `.env`, `AGENTS.md`, and `CLAUDE.md`. |
| `harness link-ascii <dir>` | Create an ASCII-only junction/symlink to `src/cf` (for Docker on Windows) and update `SOURCE_PATH` in `.env`. |
| `harness setup-skills <dir>` | Clone (or link) a skill library (`desko77`, `shirokov`, or `--local-path`) into `.agents/skills` and link it into `.claude/skills` and `.cursor/skills`. |
| `harness doctor <dir>` | Check `vrunner`, `oscript`, `ibcmd`, `gitrules`, 1C platform and `.hbk` help, Docker, ports, and workspace consistency. |
| `harness build <dir>` | Load `src/cf` into `build/ib` and write `build/reports/<name>.cf` via `ibcmd`, printing each stage's time. |
| `harness check <dir>` | Analyze `src/cf` with BSL Language Server (parallel, no 1C license), write a JSON report, and print diagnostics per severity. |

### Standalone tools

| Command | Purpose |
|---|---|
| `harness decompile <cf> <out>` | Decompile a `.cf` file into XML/BSL sources via `ibcmd` (e.g., for reading reference configurations). |
| `harness rules unpack <src> <out>` | Unpack КД 2.x rules (`.zip` or `.xml`) into editable directories and `.bsl` handlers via `gitrules export`. |
| `harness rules pack <src_dir> <out>` | Assemble unpacked `gitrules` directories back into `.xml` or `.zip` via `gitrules assembly`. |
| `harness query` | Execute a 1C query via `V83.COMConnector` (Windows-only, locally or over `--ssh user@host`) and emit JSON. |

### Binaries, infobases, extensions (agent-oriented, plain-text output)

| Command | Purpose |
|---|---|
| `harness unpack <file> <out> [--ib DIR] [--base-cf CF]` | Decompile by suffix: `.cf`, `.cfe` (registered under its internal name), `.epf`/`.erf` (designer on a cached empty infobase), `.dt` (`out/cf` + `out/cfe/<name>`). |
| `harness pack <src> <out> [--ib DIR] [--changed-from DIR] [--base-cf CF] [--no-apply]` | Build by output suffix: `.cf`, `.cfe` (import + `config apply` against the base when `--ib`/`--base-cf` is given), `.epf`/`.erf`, `.dt` (from an infobase dir). `--changed-from` imports only changed files (`import files --partial`). |
| `harness ib create <ib> [--cf CF \| --dt DT] [--cfe CFE ...]`, `ib dump`, `ib extensions` | File infobase lifecycle. |
| `harness compile <ib> [--extension NAME] [--modes ...]` | Real compiler: `DESIGNER /CheckModules`, errors merged across contexts, exit 1 on errors. |
| `harness meta find <dump> <regex> [--type T]` / `meta show <dump> <Type.Name> [--methods]` | Find objects by name/synonym (English or Russian class names); compact summary of attributes, types, tabular sections, forms, modules and methods. |
| `harness ext adopt/intercept/lint <ext_dump> <base_dump> ...` | Adopt base objects, generate `&Перед/&После/&Вместо/&ИзменениеИКонтроль` stubs, and lint the extension against the base. |
| `harness check <dump> --changed-from <pristine>` | BSL LS on changed modules only, reporting only diagnostics the change introduced. |
| `harness diff <pristine> <dump> [--patch]` | A/M/D file list (ignores `ConfigDumpInfo.xml`), optional unified diff of `.bsl`. |

Verified on platform 8.3.27 (and encoded in these commands):

- `ibcmd infobase config check` validates metadata only; BSL syntax errors pass it. Use `compile`.
- Import, `config apply` and `/CheckModules` all accept `&Перед("NoSuchMethod")` and a
  `&ИзменениеИКонтроль` body that no longer matches the base. `ext lint` catches both.
- An adopted catalog needs `<ChildObjects/>` and its `InternalInfo` generated types; an adopted
  common module must have neither. `ext adopt` mirrors the base object.
- `/DumpExternalDataProcessorOrReportToFiles` takes the root `.xml` path, not a directory.
- BSL LS crashes (`'other' has different root`) when its cwd is on another drive than the
  sources; `check` runs it from the source directory.
- Each `ibcmd` invocation costs ~12 s of start-up on a file infobase.
- Infobase users: `--ib-user/--ib-pwd` or `ONEC_IB_USR/ONEC_IB_PWD`.

### Windows sandbox workaround

| Command | Purpose |
|---|---|
| `harness fix-permissions <dir>` | Windows-only: re-grant the owner, re-enable inheritance, and reapply low integrity labels after moving a workspace used inside a low-integrity sandbox. Must run outside the sandbox; `--dry-run` prints the `icacls` calls without changing anything. |

## Key Options & Notes

- `init --dry-run --force --ide --platform-tools-js PATH --platform-dir PATH --ipc-port N --mcp-python PATH --indexing-mode fast|full`
- `setup-skills --source desko77|shirokov|local --local-path PATH --update`
- `doctor --json --strict`
- `build --src PATH --ib PATH --out PATH --skip-import --skip-save --no-check --ibcmd PATH`
- `check --src PATH --config PATH --report-dir PATH --fail-on-error`
- `--mcp-python` points the `bsl-platform-help` server at the Python interpreter that has `mcp_bsl_context` installed.
- `--indexing-mode full` enables semantic search in `bsl-atlas` using local Ollama (`host.docker.internal:11434`, `qwen3-embedding:8b`) or OpenRouter (`*_PROVIDER=openrouter` + `OPENROUTER_API_KEY`). Default `fast` mode uses SQLite/FTS only.
- `init` is idempotent: existing files are skipped unless `--force` is given; `.vscode/settings.json` and `.gitignore` are merged, never replaced.

## Development

```bash
ruff check src tests && ruff format --check src tests
mypy
pytest
```

## Provisional values

Some external key names are not confirmed by upstream docs and are marked `PROVISIONAL` in the source:
the BSL LS `$schema` URL and the VS Code `1c-platform-tools.ipc.port` key.

`autumn-properties.json` follows the `vrunner` 3.0 layout: every setting sits under `vrunner.*` and is
resolved by walking up the command path (`vrunner.ibconnection` applies to `cf load`, `cf compile`,
and `validate syntax-check`).

See [ARCHITECTURE.md](ARCHITECTURE.md) and [OPEN_ISSUES.md](OPEN_ISSUES.md).
