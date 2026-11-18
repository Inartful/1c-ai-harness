"""Generator for the `AGENTS.md` five-phase state machine."""

AGENTS_MD = """\
# 1C Agent Protocol

Deterministic state machine. Run phases in order. Never skip a gate.

## Global Rules
- Minimal diffs only. Touch only what the task requires.
- NEVER edit `*.xml` with text tools (edit_file, write_file, sed). Use JSON-DSL skills.
- NEVER start a build/load while another is running. Long runs are normal; wait.
- Prefer incremental load (`--increment`) over full load.
- Do not trust first-pass output. Verify (Phase 5) before reporting.

## Tool Priority
1. `bsl-platform-help`: platform types/methods (ground truth).
2. `bsl-atlas-rag`: this configuration's code, metadata, call graph.
3. Skills `1c-bsp-api`, `1c-ssl-patterns`: BSP (БСП) APIs and patterns.
4. `bsl-lsp`: diagnostics, references, hover.
5. `vrunner-devops`: convert, load, validate, compile.

When an MCP server is not exposed in the session, use the CLI fallbacks below — they are
first-class tools, not a workaround.

## CLI Fallbacks
Skill scripts are Python 3 and live under `.agents/skills/skills/<skill>/scripts/`:
- `1c-config-index/scripts/config-index.py` — build or query `data/config-index.json`
- `1c-meta-compile/scripts/meta-compile.py` — create metadata from JSON-DSL
- `1c-meta-edit/scripts/meta-edit.py` — edit metadata from JSON-DSL
- `1c-meta-validate/scripts/meta-validate.py`, `1c-cf-validate/scripts/cf-validate.py`,
  `1c-bsl-validate/scripts/bsl-validate.py`,
  `1c-query-validate/scripts/query-validate.py` — validators

Run them from the workspace root, e.g.
`python .agents/skills/skills/1c-cf-validate/scripts/cf-validate.py --help`.

## Phase 1: Bootstrap & Decompile
Trigger: `*.cf|*.cfe|*.epf|*.erf` in `./inbox/`, or `./src/cf/Configuration.xml` missing.
Chain: `vrunner cf convert` (or `cf decompile`) -> skill `1c-config-index` ->
`bsl-atlas-rag.reindex()`.
Gate: no code edits until `./data/config-index.json` exists and the atlas index is refreshed.

## Phase 2: Routing & BSP Grounding
Trigger: task to add or change functionality.
Chain: skill `1c-config-router` -> check `1c-bsp-api`, `1c-ssl-patterns` ->
`codesearch`, `get_function_context`.
Gate: NEVER reimplement printing, files, mail, long operations, RLS when BSP provides them.

## Phase 3: Platform Syntax Verification
Trigger: before writing or changing any procedure/function in `*.bsl`.
Chain: `search` -> `get_members` -> `info` / `get_constructors`; check `get_strict_typing_info`.
Gate: no platform type member or constructor used without signature check.
Respect context directives, e.g. `&НаСервере`, `&НаКлиенте`, `&НаСервереБезКонтекста`.
Example type names: `СправочникОбъект`, `ТаблицаЗначений`.

## Phase 4: JSON-DSL Compilation
Trigger: create/change metadata, form, role, MXL, DCS (СКД), or extension (`.cfe`).
Chain: read via `1c-*-info` / `1c-meta-decompile` -> write short JSON-DSL -> compile with
`1c-meta-compile`, `1c-form-compile`, `1c-skd-compile`, `1c-role-compile`, `1c-cfe-patch-method`.
Gate: direct `.xml` edits forbidden. If the object is vendor-locked, produce a manual
instruction package with `1c-change-package`.

## Phase 5: Verification & Build
Trigger: any change under `./src/` before reporting to the user.

Fast gate (primary, offline; run first):
1. `1c-meta-validate`, `1c-cf-validate`, `1c-bsl-validate`, `1c-query-validate` against
   `config-index.json`.
2. `bsl-lsp` diagnostics on the files you changed.

Platform gate (slow; needs 1C and a license):
1. `vrunner --settings ./autumn-properties.json cf load --increment ./src/cf`
2. `vrunner --settings ./autumn-properties.json validate syntax-check --mode ExtendedModulesCheck`
3. `vrunner --settings ./autumn-properties.json cf compile --src ./src/cf ./build/reports/<name>.cf`

Rules:
- If `harness` is on PATH, prefer `harness build` and `harness check`: they drive `ibcmd` and
  BSL Language Server and are about five times faster than the vrunner steps above.
- `harness compile <ib> [--extension <name>]` runs the real platform compiler
  (`DESIGNER /CheckModules`, seconds); `harness check --changed-from <pristine_dump>` lints only
  changed modules and reports only NEW issues.
- Extensions: run `harness ext lint <ext_dump> <base_dump>`. The platform silently accepts an
  interception of a non-existent method and a stale `&ИзменениеИКонтроль` body; only lint
  catches them.
- `autumn-properties.json` is vrunner 3.0 format: settings live under `vrunner.*` and are resolved
  by walking up the command path (`vrunner.cf.load.*` -> `vrunner.cf.*` -> `vrunner.*`). A flat 2.6
  file is silently ignored — and then `syntax-check` checks an empty temporary infobase.
- Options go BEFORE positional arguments: `cf compile --src ./src/cf out.cf`, not
  `cf compile out.cf --src ./src/cf`. After a positional argument the parser fails with
  `Ошибка чтения параметров команды`.
- If `syntax-check` output contains `Создаётся временная информационная база`, the gate FAILED:
  the check proved nothing. Fix the connection and rerun.
- After `infobase init` the first `cf load --increment` is a full load: the incremental index key
  does not match yet. Do not read the long first load as a hang.
- Vendor configurations return exit code 1 with thousands of pre-existing `ExtendedModulesCheck`
  remarks. Do not chase a global green: filter the remarks for modules and objects you created or
  changed, and fail only on those.
- `infobase update` may exit 0 on a syntax error; syntax-check is mandatory.
- Do not close the task while any validator, linter, or syntax error in changed code remains.

## Exchange Rules (КД 2.x) & Live Infobase Queries
- **Reference configs:** use `harness decompile <file.cf> <out_dir>` to export source/target
  `.cf` into readable XML/BSL via `ibcmd`.
- **КД 2.x rules:** never edit monolithic `ExchangeRules.xml` directly. Run
  `harness rules unpack <rules.zip> <dir>` (uses `gitrules export`), edit `.bsl` handlers,
  validate syntax with `bsl-language-server analyze -s <dir>`, and pack back with
  `harness rules pack <dir> <out.zip>`.
- **Direction rule:** `ПослеЗагрузки` / `ПриЗагрузке` handlers for `A -> B` execute on the
  **target** base `B`. In a B-side rules archive, `A -> B` is usually
  `CorrespondentExchangeRules.xml`, while `ExchangeRules.xml` is `B -> A`.
- **Live queries:** use `harness query --server <Srvr> --ref <Ref> -q "<1C query>"`
  (returns JSON via `V83.COMConnector`). Credentials and SSH jump host (`ONEC_SSH`, `ONEC_USR`,
  `ONEC_PWD`) are read automatically from process env or Windows `HKCU\\Environment` — do not
  ask the user for passwords if already configured. Pass `--ssh local` to bypass `ONEC_SSH`.
"""


def render_agents_md() -> str:
    """Return the `AGENTS.md` content.

    Returns:
        Markdown text of the five-phase protocol.
    """
    return AGENTS_MD
