# Open issues and decisions

State after the first end-to-end session on a real vendor configuration
(26 486 files, 2 GB dump, 2.1 GB file infobase, platform 8.3.27.2342 x64).

## Decisions already taken

- `autumn-properties.json` is vrunner **3.0** format: settings live under `vrunner.*`
  and are resolved by walking up the command path. A flat 2.6 file is silently ignored.
- Compile and analysis use the fast paths: `harness build` (`ibcmd`) and `harness check`
  (BSL Language Server). Measured: 3.5 min vs 17 min and 5 min vs 26 min.
- `--mcp-python` records the interpreter that has `mcp_bsl_context`; `python` from PATH is a
  Microsoft Store stub on this machine.
- DSH permission preset `1c-toolchain` (danger-full-access + never) is what makes 1C run at all.

## Open: Windows low-integrity sandbox

- Moving or copying a workspace tree breaks inherited low-integrity labels; `harness fix-permissions`
  is the workaround (re-enables inheritance and reapplies the low integrity label recursively).
- To roll back low-integrity labels on a workspace:
  `icacls "<workspace>" /setintegritylevel "(OI)(CI)Medium" /T`

## Open: upstream (vrunner 3.0.2 / skills)

- `cf compile` and `cf load` parse no options after a positional argument; options must come
  first. Documented in `AGENTS.md`, but it is an upstream parser bug.
- `--increment` after `infobase init` always performs a full load: the cache key differs.
  Documented; needs an upstream fix.
- `ExtendedModulesCheck` has no baseline diff mode, so vendor configurations cannot be green.
- `1c-meta-edit` drops the `comment` key on `add-attribute` and inserts children at the end of
  their type group instead of alphabetically. Both in `claude-code-skills-1c`, not here.
- No baseline run of `ExtendedModulesCheck` on the pristine `.cf` (~35 min). The additive-only
  diff is the current argument for "no new warnings".

## Open: harness backlog

- Done: `harness check --changed-from` analyzes only changed modules and reports only new
  diagnostics; `harness pack --changed-from` imports only changed files. The whole-dump
  `harness check` report is still ~310 MB and not rotated; `harness build` still imports fully.
- `ext adopt` was verified round-trip for `Catalog` and `CommonModule`. Other classes reuse the
  base `InternalInfo` generically; `pack` will reject the XML if a class needs more.
- Form adoption is not automated (adopted forms carry the whole base form tree).
- `ext lint` compares `&ИзменениеИКонтроль` bodies ignoring blank lines, whitespace and full-line
  comments; the platform's own comparison rules are not documented.
- `unpack .cfe --ib` and `ib create --cfe` load the extension twice (temp name, then the
  internal name read from the export): ~36 s extra per extension.
- `SPEC.md` still describes the old assumptions (flat settings, ghcr images,
  `--search-default-mode hybrid`, `-ExtendedModulesCheck`). It is a historical blueprint;
  `README.md` is current. Decide whether to update or archive it.
- No CI (tests/ruff/mypy run locally only), no git remote.
- `1c-platform-tools` MCP server (`init --ide`) is not configured; the VS Code extension is
  not installed.

## Open: data quality

- Atlas `full` mode has not been indexed on the real configuration yet; only a synthetic
  smoke test proved that Ollama embeddings work (2 chunks, `chromadb.code = 2`).
- No accuracy comparison of `fast` vs `full` on real code (top-1/top-3 on a query set).
- `mcp_bsl_context` runs in `keyword` mode; semantic/hybrid needs the `[local]` extras
  (torch, sentence-transformers) or an embedding API.
- BSP skills cover versions 3.1.11 and 3.2.1; the version of the target configuration has
  not been checked against them.
- `vrunner-mcp` starts but its MCP handshake was not confirmed (only the notification).
