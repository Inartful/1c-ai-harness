# Architecture

## Layers

```
cli.py            Typer commands: parse args, call a service, render, map errors to exit codes
report.py         Rich tables
services/         init, link_ascii, setup_skills, doctor (business logic, injectable runners)
generators/       pure functions: build validated models, render text (AGENTS.md, packagedef)
models/           Pydantic v2 schemas for every generated config
system/           runner (safe subprocess), links (symlink/junction), platform, docker
ports.py paths.py deterministic port allocation, ASCII detection, project hash
errors.py         HarnessError hierarchy
```

Dependencies point downward only. Services take a `CommandRunner` (and, for `doctor`, `which` and
a port probe) as parameters, so tests never run real external tools.

## Key decisions

- **Models are the single source of truth.** Every JSON/YAML file is rendered from a frozen
  `extra="forbid"` Pydantic model, using aliases for on-disk key names such as `log-output`.
- **One execution path.** All external commands go through `system.runner.run`: `shell=False`,
  mandatory timeout, PATH resolution, and typed errors (`ToolNotFoundError`,
  `CommandTimeoutError`, `CommandFailedError`).
- **Port isolation.** The IPC port starts at `40241 + sha256(workspace path) % 760` and probes
  forward with wrap-around. An existing `.vscode/settings.json` port is reused if free. The same
  port is written to `.env`, `.vscode/settings.json` and both MCP files. `doctor` verifies they agree.
- **Safe writes.** `FileWriter` writes atomically (temp file plus replace), skips differing files
  unless forced, and supports dry-run. Merged files (`.vscode/settings.json`, `.gitignore`) are
  updated in place; a `.vscode/settings.json` with comments is left untouched with a warning.
- **Links.** On Windows a junction (`mklink /J`) is preferred and `/D` is the fallback. A link is
  never replaced if it points elsewhere. `CLAUDE.md` is a symlink, with a copy fallback.
- **Doctor is read-only.** Failures are required tools and platform files. Warnings are optional
  tools, Docker and Docker-unsafe paths. `--strict` promotes warnings to failures.

## Known limits

- Port probing is racy: a port can be taken between the probe and IDE start-up.
- Port ownership cannot be determined, so a busy port is reported as a warning.
- Provisional external keys and image tags are listed in the README.
- `mklink` arguments are passed through `cmd /c`; unusual path characters (such as `&`) were not tested.
