# SPEC: `1c-ai-harness`

> **Note:** Historical design blueprint from Phases 2–3. For current CLI commands and usage, see [README.md](README.md).

Status: **Approved (open questions resolved with the proposed defaults). Implemented in Phases 2-3.**

Deviations from the draft: the IPC port busy check in `doctor` is a warning (port ownership cannot be determined); `--ide` without `--platform-tools-js` writes a `<PLATFORM_TOOLS_JS>` placeholder and warns.

## 1. Purpose and scope

`1c-ai-harness` (CLI entry point: `harness`) is a deterministic **workspace bootstrapper, config generator and environment validator**. It does not parse 1C XML, build MCP servers or implement RAG. It scaffolds and wires up existing tools:

| Component | Role |
|---|---|
| `vanessa-runner 3.0` (`vrunner`, `vrunner-mcp`) | `.cf/.cfe/.epf` convert, decompile, load, compile, validate |
| `Desko77/claude-code-skills-1c` (or `Nikolay-Shirokov/cc-1c-skills`) | JSON-DSL skills, rules, BSP references |
| `Desko77/mcp-bsl-platform-help-context` | Platform syntax grounding from `.hbk` help |
| `Arman-Kudaibergenov/bsl-atlas` | Structural and semantic index of `src/cf` |
| `bsl-language-server` | Static analysis (verification gate) |
| `yellow-hammer.1c-platform-tools` (optional) | IDE panel and IPC MCP server (default port 40241) |

Out of scope: running vrunner pipelines, indexing, embedding generation, and the LLM-generated function "stickers" in bsl-atlas.

## 2. Tech stack and quality gates

- Python 3.12+, Typer, Pydantic v2 (`extra="forbid"`, `frozen=True`), Rich, Pytest, Ruff, `mypy --strict`.
- `pathlib.Path` only. No `Any` unless justified in a comment.
- Every external process goes through `harness.system.runner` (timeout, return-code check, structured errors).
- Single source of truth: every generated config is built from a Pydantic model and serialized with `model_dump_json(by_alias=True, indent=2)`. YAML is emitted via a Pydantic model and `yaml.safe_dump`.
- Writes are idempotent. Existing files are skipped unless `--force` is given. `--dry-run` prints the plan.
- Gates: `ruff check`, `ruff format --check`, `mypy --strict src tests`, `pytest`.

## 3. Package layout

```
1c-harness/
├─ pyproject.toml
├─ README.md
├─ ARCHITECTURE.md
├─ SPEC.md
├─ src/harness/
│  ├─ __init__.py
│  ├─ cli.py                  # Typer app, command signatures only
│  ├─ errors.py               # HarnessError hierarchy
│  ├─ models/
│  │  ├─ autumn.py            # AutumnProperties
│  │  ├─ mcp.py               # McpConfig, McpServer
│  │  ├─ bsl_ls.py            # BslLanguageServerConfig
│  │  ├─ compose.py           # ComposeFile, ComposeService
│  │  ├─ env.py               # EnvFile
│  │  ├─ vscode.py            # VSCodeSettings (IPC port merge)
│  │  └─ workspace.py         # WorkspaceLayout, constants
│  ├─ generators/
│  │  ├─ autumn.py  mcp.py  bsl_ls.py  compose.py  env.py  packagedef.py
│  │  ├─ agents_md.py         # AGENTS.md template (5-phase state machine)
│  │  └─ vscode.py
│  ├─ ports.py                # deterministic IPC port allocation
│  ├─ paths.py                # ASCII-path detection, project hash
│  ├─ system/
│  │  ├─ runner.py            # safe subprocess wrapper
│  │  ├─ links.py             # symlink / junction manager
│  │  ├─ platform.py          # 1cv8 / .hbk discovery
│  │  └─ docker.py            # docker daemon probe
│  ├─ services/
│  │  ├─ init.py  link_ascii.py  setup_skills.py  doctor.py
│  └─ report.py               # Rich rendering
└─ tests/
```

## 4. Workspace tree created by `harness init`

```
<target_dir>/
├─ AGENTS.md                      # 5-phase state machine
├─ CLAUDE.md -> AGENTS.md         # symlink (fallback: copy on failure, with warning)
├─ packagedef                     # OneScript/opm marker for 1C Platform Tools
├─ autumn-properties.json
├─ .bsl-language-server.json
├─ .mcp.json
├─ .cursor/mcp.json
├─ .vscode/settings.json          # merged, not overwritten
├─ docker-compose.yml
├─ .env
├─ .gitignore                     # appended: build/, data/*.db, .env
├─ inbox/                         # drop .cf/.cfe/.epf/.erf here
├─ src/{cf,cfe,epf}/
├─ data/                          # config-index.json, bsl_index.db
├─ build/{ib,reports}/
└─ .agents/skills/                # filled by setup-skills
```

`.agents/rules/` is created too, as the source describes it. `.gitkeep` files keep empty directories in git.

## 5. Pydantic models

All models use `model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)`. Aliases keep the on-disk key names exact.

### 5.1 `AutumnProperties` → `autumn-properties.json`
```python
class AutumnProperties(BaseModel):
    ibconnection: str = "/F./build/ib"
    ibcmd: bool = True
    log_output: Literal["stderr", "stdout"] = Field("stderr", alias="log-output")
    src: str = "./src/cf"                  # source directory
    reports_dir: str = Field("./build/reports", alias="reports-dir")
```
Open question: the exact vanessa-runner 3.0 key names beyond `ibconnection`, `ibcmd` and `log-output`. The source document gives only those three, so `src` and `reports-dir` are provisional. See §10.

### 5.2 `McpConfig` → `.mcp.json`, `.cursor/mcp.json`
```python
class McpServer(BaseModel):
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    url: str | None = None                 # exactly one of command / url (validator)

class McpConfig(BaseModel):
    mcp_servers: dict[str, McpServer] = Field(alias="mcpServers")
```
Servers emitted (from the source blueprint):

| Key | Transport | Details |
|---|---|---|
| `vrunner-devops` | stdio | `vrunner-mcp --settings ./autumn-properties.json` |
| `bsl-platform-help` | stdio | `python -m mcp_bsl_context -p <platform_dir> --search-default-mode hybrid` |
| `bsl-atlas-rag` | http | `http://localhost:8000/mcp` |
| `bsl-lsp` | stdio | `bsl-language-server --mcp -c ./.bsl-language-server.json` (JVM flag `-Xmx4g` via `JAVA_TOOL_OPTIONS`) |
| `mcp-1c-platform-tools` | stdio, `--ide` only | `ONEC_IPC_HOST=127.0.0.1`, `ONEC_IPC_PORT=<allocated>`, `ONEC_IPC_TOKEN=<random hex>` |

`<platform_dir>` is the detected `1cv8` install directory. If none is found, a `<PLATFORM_DIR>` placeholder is written and `init` warns. `mcp-1c-platform-tools` needs the extension's `out/src/index.js` path, which cannot be discovered reliably. It is emitted only when `--ide` is set, and the path comes from `--platform-tools-js`.

### 5.3 `BslLanguageServerConfig` → `.bsl-language-server.json`
```python
class BslLanguageServerConfig(BaseModel):
    language: Literal["ru", "en"] = "ru"   # emitted as "language"
    diagnostics: DiagnosticsConfig         # parameters: {}, mode / subsystem filter
    # $schema emitted via alias "$schema"
```
Key names are checked against the BSL LS configuration schema, which the model references via `$schema`. The default is language `ru` plus a conservative diagnostics block.

### 5.4 `ComposeFile` → `docker-compose.yml`
```python
class ComposeService(BaseModel):
    image: str
    ports: list[str] = []
    volumes: list[str] = []
    environment: dict[str, str] = {}
    restart: Literal["no", "unless-stopped"] = "unless-stopped"

class ComposeFile(BaseModel):
    services: dict[str, ComposeService]
```
Services:
- `bsl-atlas`: bind mount `${SOURCE_PATH}:/data/source:ro` and `./data:/data/index`, port `8000:8000`, embedding provider variables taken from `.env`.
- `bsl-platform-help`: bind mount `${PLATFORM_DIR}:/opt/1cv8:ro`, `streamable-http --port 8080`, mode `keyword`.

Image names and tags are config values (`--atlas-image`, `--help-image`) with documented defaults. No images are verified at generation time.

### 5.5 `EnvFile` → `.env`
```python
class EnvFile(BaseModel):
    source_path: Path           # SOURCE_PATH
    platform_dir: Path | None   # PLATFORM_DIR
    onec_ipc_port: int          # ONEC_IPC_PORT, 40241..40999
    embedding_mode: Literal["fast", "full"] = "fast"
    embedding_api_key: SecretStr | None = None
```
Rendered as sorted `KEY=value` lines. `link-ascii` rewrites only `SOURCE_PATH`.

### 5.6 `VSCodeSettings`
Parsed with `extra="allow"` so that user settings survive. The harness sets only the key `1c-platform-tools.ipc.port`. This key name is **to be confirmed** against the extension (§10).

## 6. IPC port allocation (`ports.py`)

```
BASE = 40241, SPAN = 760  (40241..41000)
start = BASE + int(sha256(resolved_posix_path).hexdigest(), 16) % SPAN
probe start, start+1, ... (wrap within span); first port with bind-test success wins
```
- Reuse: if `.vscode/settings.json` already has a port, `init` reuses it when it is still free or held by this workspace.
- Port sync: the same integer is written to `.vscode/settings.json`, `ONEC_IPC_PORT` in `.mcp.json` and `.cursor/mcp.json`, and `.env`. A test asserts that all four match, which prevents `WORKSPACE_MISMATCH`.
- Known race: a port can be taken between the probe and IDE startup. The hash makes collisions unlikely but not impossible.

## 7. CLI signatures

```python
app = typer.Typer(no_args_is_help=True)

@app.command() def init(
    target_dir: Path,
    force: bool = False,
    dry_run: bool = False,
    ide: bool = False,                       # emit mcp-1c-platform-tools + .vscode port
    platform_tools_js: Path | None = None,
    platform_dir: Path | None = None,        # override 1cv8 autodetect
    ipc_port: int | None = None,             # override auto allocation
) -> None: ...

@app.command("link-ascii") def link_ascii(
    target_dir: Path,
    link_root: Path = Path("C:/bsl-src"),
    dry_run: bool = False,
) -> None: ...

@app.command("setup-skills") def setup_skills(
    target_dir: Path,
    source: SkillSource = SkillSource.DESKO77,   # desko77 | shirokov | local
    local_path: Path | None = None,              # use existing checkout (link, no clone)
    ref: str | None = None,
    update: bool = False,
) -> None: ...

@app.command() def doctor(
    target_dir: Path,
    json_output: bool = Option(False, "--json"),
    strict: bool = False,                        # warnings fail the run
) -> None: ...
```

Exit codes: `0` ok, `1` check failed or operation error, `2` usage error (Typer default).

### Behavior

**`init`**
1. Validate `target_dir` (create if missing). Refuse a non-empty dir without `--force` only when a generated file would be overwritten.
2. Detect the platform dir, allocate the port, then build all models, which validates them.
3. Write files atomically (temp file plus `replace`). Create the `CLAUDE.md` symlink, falling back to a copy and a warning when symlinks are not permitted.
4. If the path is non-ASCII or contains spaces, print a hint to run `link-ascii`. The `.env` `SOURCE_PATH` is initially the raw path to `src/cf`.

**`link-ascii`**
1. Check whether `<target_dir>/src/cf` has a non-ASCII character or a space. If not, report "not needed" and exit 0.
2. Compute `project_hash = sha256(resolved_posix_path)[:8]` and the link at `<link_root>/<hash>`.
3. On Windows create a directory junction (`cmd /c mklink /J`, which needs no admin rights), falling back to `mklink /D`. On POSIX use `os.symlink`.
4. If the link exists and points to the same target, do nothing. If it points elsewhere, fail without overwriting.
5. Update `SOURCE_PATH` in `.env`.

**`setup-skills`**
1. `git clone --depth 1` into `.agents/skills/`, or link `--local-path`. With `--update`, run `git pull --ff-only`.
2. Link `.claude/skills` and `.cursor/skills` to `.agents/skills`. Use a junction on Windows and a symlink elsewhere. Skip existing correct links and fail on conflicting real directories.
3. The upstream installer (`--link`, `switch.py --runtime`) is not invoked. The harness creates the links itself and prints the `switch.py` hint.
4. Repository URLs are constants in `models/workspace.py`. The `git` tool is required.

**`doctor`** (read-only). Each check returns `CheckResult(name, status: ok|warn|fail, detail, hint)`:

| Check | Fail or warn condition |
|---|---|
| `vrunner` | not on PATH, or `vrunner --version` is not 3.x → fail |
| `oscript` | missing, or version below 2.0 → fail |
| `ibcmd` | missing → warn (needed for `"ibcmd": true`) |
| `1cv8` platform | no installation found → fail |
| `.hbk` help files | none found in the platform `bin/` dir → fail |
| `docker` | `docker info` fails or times out → warn |
| `git` | missing → warn |
| `java` / `bsl-language-server` | missing → warn |
| IPC port | configured port busy and not owned by this workspace → fail |
| port consistency | `.mcp.json`, `.cursor/mcp.json`, `.vscode/settings.json`, `.env` disagree → fail |
| workspace layout | required directories or files missing → fail |
| ASCII path | non-ASCII or space in the `SOURCE_PATH` → warn |

## 8. Safe execution layer (`system/runner.py`)

```python
class CommandResult(BaseModel): argv: tuple[str, ...]; returncode: int; stdout: str; stderr: str; duration_s: float

def run(argv: Sequence[str], *, timeout_s: float, cwd: Path | None = None,
        check: bool = True, env: Mapping[str, str] | None = None) -> CommandResult
```
- `shell=False`, argv list only. `mklink` is invoked as `["cmd", "/c", "mklink", ...]`.
- The executable is resolved with `shutil.which`. A missing executable raises `ToolNotFoundError`.
- Raises `CommandTimeoutError` (the process is killed), `CommandFailedError` (non-zero exit when `check=True`), or `ToolNotFoundError`. All derive from `HarnessError`, and the CLI converts them to a Rich error panel plus exit code 1.
- Timeouts are mandatory (no default). Output is captured as UTF-8 with `errors="replace"`.
- Tests mock the runner, so they never invoke real tools.

## 9. `AGENTS.md` outline (concise English)

Generated from a Python template (`generators/agents_md.py`). Russian appears only in literal BSL and metadata examples.

```
# 1C Agent Protocol
Rules: minimal diffs; never edit *.xml by text tools; never re-run a long build while one is active; one phase at a time.
## Tool priority
## Phase 1 Bootstrap & Decompile    trigger / chain / gate
## Phase 2 Routing & BSP Grounding
## Phase 3 Platform Syntax Verification   (&НаКлиенте/&НаСервере, СправочникОбъект examples)
## Phase 4 JSON-DSL Compilation
## Phase 5 Verification & Build
```
Gates, taken from the blueprint:

| # | Gate |
|---|---|
| 1 | No code edits until `data/config-index.json` exists and bsl-atlas is reindexed. |
| 2 | Never reimplement printing, files, mail, long operations or RLS if BSP provides it. |
| 3 | No platform type, method or constructor use without `get_members` / `info` confirmation. |
| 4 | No direct `.xml` edits. Use the JSON-DSL skills or `1c-change-package`. |
| 5 | Run validators, then BSL LS, then `vrunner cf load --increment`, then `vrunner validate syntax-check -ExtendedModulesCheck`, then `vrunner cf compile`. The task stays open while any error remains. |

The document says the `vrunner infobase update` command may return code 0 on a module with a syntax error. Phase 5 therefore requires the explicit `syntax-check` step.

## 10. Open questions (need your decision)

1. **Unverified external schemas.** The source gives exact keys only for `ibconnection`, `ibcmd`, `log-output`, `ONEC_IPC_*` and `SOURCE_PATH`. For other keys I plan to rely on the upstream docs for vanessa-runner, BSL LS, and the Platform Tools extension (`ipc.port` setting name). I'll flag every provisional key in code comments and in `ARCHITECTURE.md`. OK?
2. **Docker images.** The source names no image tags for `bsl-atlas` or `mcp-bsl-platform-help-context`. Proposal: configurable defaults (`ghcr.io/...` placeholders, to be replaced by real tags I find in upstream READMEs during Phase 2). OK?
3. **Junction vs `mklink /D`.** The brief mentions `mklink /D`, which needs admin rights or Developer Mode. Proposal: prefer a junction (`/J`) for the ASCII link and `.agents/skills` links, then fall back to `/D`.
4. **`CLAUDE.md` symlink on Windows.** File symlinks also need privileges. Proposal: fall back to a copy and warn.
5. **YAML dependency.** Add `PyYAML` plus `types-PyYAML` for `docker-compose.yml`. OK?
6. **Package name.** Distribution name `1c-ai-harness` and import package `harness` (console script `harness`). OK?
7. **Platform Tools MCP.** Emit `mcp-1c-platform-tools` only with `--ide`, as proposed above?

## 11. Phase plan

| Phase | Deliverable |
|---|---|
| 2 | `pyproject.toml`, `models/`, `generators/`, `ports.py`, unit tests, `mypy --strict` and `pytest` green |
| 3 | `system/*`, `services/*`, `cli.py` (`init`, `link-ascii`, `setup-skills`, `doctor`), tests with a mocked runner |
| 4 | Full audit: pytest, ruff, mypy; finalize `README.md` and `ARCHITECTURE.md` |
