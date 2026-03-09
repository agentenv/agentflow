# AgentFlow

AgentFlow is a Python orchestrator for `codex`, `claude`, and `kimi` agents with Airflow-style DAGs, parallel scheduling, multiple execution targets, and a live web console for traces and artifacts.

## Features

- Airflow-like pipeline definitions in Python and YAML
- Parallel DAG execution with dependency-aware scheduling
- Per-node selection of model, provider, tools policy, MCP servers, and skills
- Execution targets for local processes, containers, and AWS Lambda
- Per-node output capture as either final response or full trace
- Post-run success criteria including output contains text, file exists, file contains text, and file non-empty
- Run queueing, retries, retry backoff, cancellation, rerun, and persisted run recovery
- Web UI for DAG state, attempt history, JSONL trace parsing, stdout/stderr, and node artifacts
- Redacted per-node `launch.json` artifacts for debugging shell bootstrap, container wrapping, and Lambda payloads
- API endpoints for validation, launch, events, artifacts, cancel, rerun, and health checks

## Why this shape

This project was built in this repo, and the integrations were informed by:

- OpenAI Codex CLI patterns in `reference/codex`
- Claude Code web and stream patterns in `reference/claude-code-telegram`
- Moonshot Kimi CLI protocol and UI patterns in `reference/kimi-cli`

Those references shaped the trace parsers, adapter contracts, and frontend event model.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

After installation, you can invoke the CLI either as `agentflow ...` or `python -m agentflow ...`.
If you are not inside an activated virtualenv, prefer `.venv/bin/python -m agentflow ...` from the repo root or `python3 -m agentflow ...`.

## Quick start

Scaffold a pipeline from the bundled templates:

```bash
agentflow templates
agentflow init > pipeline.yaml
agentflow init kimi-smoke.yaml --template local-kimi-smoke
agentflow init kimi-shell-init-smoke.yaml --template local-kimi-shell-init-smoke
agentflow init kimi-shell-wrapper-smoke.yaml --template local-kimi-shell-wrapper-smoke
```

Use `agentflow templates` to list the bundled starters with short descriptions, example source files, and the matching `agentflow init --template ...` command.
The default `pipeline` template is a generic Codex/Claude/Kimi DAG. The `local-kimi-smoke` template is the same real-agent local Codex plus Claude-on-Kimi smoke DAG used by the repo's verification scripts, so it is a fast way to bootstrap a known-good local setup into your own workspace. When you want that same local smoke flow with explicit `shell: bash`, `shell_login: true`, `shell_interactive: true`, and `shell_init: kimi` wiring instead of the shorthand `bootstrap: kimi`, use the `local-kimi-shell-init-smoke` template. When you want the same flow with an explicit `target.shell: "bash -lic 'command -v kimi >/dev/null 2>&1 && kimi && {command}'"` wrapper, use the `local-kimi-shell-wrapper-smoke` template.

Validate a pipeline:

```bash
agentflow validate examples/pipeline.yaml
```

Inspect the resolved launch plan before running it:

```bash
agentflow inspect examples/local-real-agents-kimi-smoke.yaml
agentflow inspect examples/local-real-agents-kimi-smoke.yaml --output json-summary
agentflow inspect examples/pipeline.yaml --node review --output json
```

The default summary view now includes resolved per-node model, tools, capture, skills, MCP server names, provider details, and auth source hints when they are set, which makes it easier to verify mixed Codex, Claude, and Kimi launch configs before you execute a run.
On an interactive terminal, `agentflow inspect` now defaults to that human-readable summary; when stdout is redirected or piped, it falls back to the full JSON inspection payload so shell wrappers can parse launch details without adding `--output json`.
Those auth hints call out whether a node will rely on `node.env`, `provider.env`, the current environment, local shell bootstrap such as `target.bootstrap: kimi` or `target.shell_init: kimi`, or Codex CLI login fallback, so it is easier to spot hidden local prerequisites before launch.
When a local Claude node relies on Kimi's Anthropic-compatible bootstrap, the auth hint now keeps the Kimi helper first even if `ANTHROPIC_API_KEY` is already present in `node.env`, `provider.env`, or the current environment, because that helper runs last in the prepared shell and becomes the effective launch source.
Those same `inspect` and `doctor` auth probes also honor launch-only shell inputs from `node.env` and `provider.env` while checking login or interactive Bash startup, so env-gated bridges such as `~/.profile` sourcing a file named by `AGENTFLOW_KIMI_ENV_FILE` are recognized before launch instead of being reported as missing credentials.
They also recognize non-interactive Bash wrappers that preload credentials through `BASH_ENV`, such as `target.shell: env BASH_ENV=$HOME/.anthropic.env bash -c '{command}'`, as long as that file really exports the provider key.
For local Codex nodes that run through a `kimi` shell bootstrap, the same auth summary now also calls out that bootstrap even when `OPENAI_API_KEY` already comes from the environment, and it names that bootstrap as the Codex CLI login path when no key is injected.
When a resolved provider also needs to override conflicting shell values such as `ANTHROPIC_BASE_URL`, `inspect` now warns that the node launch env will replace the current value and names whether that override came from `node.env`, `provider.env`, or provider-derived settings such as `provider.base_url`, which makes mixed provider shells easier to debug before launch.
For local nodes, it also surfaces shell bootstrap details such as `bootstrap`, `shell`, login and interactive flags, the active bash login startup file (`~/.bash_profile`, `~/.bash_login`, or `~/.profile`), and `shell_init`, so Kimi-backed wrappers are easier to confirm without decoding the full launch command. Those login and interactive flags are inferred from direct shell wrappers such as `bash -lic` too, not only from structured `shell_login` and `shell_interactive` fields. The summary and `json-summary` forms now also include the bash login file presence matrix (`~/.bash_profile`, `~/.bash_login`, `~/.profile`) for the effective bootstrap home, so it is easier to confirm whether a node is really falling back through `~/.profile` or shadowing it with another login file. When a bash login wrapper is configured but no user startup file exists, `inspect` now shows `startup=none` and emits a warning so missing `~/.bash_profile` / `~/.profile` bridges are easier to spot before launch. When that warning means the node really needs a login-shell bridge, `inspect` now also includes the ready-to-paste shell bridge suggestion inline in summary output and as `shell_bridge` in JSON outputs, so you do not need to switch to `doctor` just to grab the fix. Inline secret assignments in `shell_init` or shell wrappers are redacted in both `inspect` output and persisted `launch.json` artifacts. Those auth hints also recognize sourced shell files such as `target.shell_init: source ~/.anthropic.env` or `target.shell: bash -lc 'source ~/.anthropic.env && {command}'` when that file exports the provider key.
That same warning and shell-bridge hint now also appear when a local login-shell node still expects provider auth from bash startup files and the active login file shadows the real `~/.bashrc` bridge, which makes plain Claude-on-Anthropic or local Kimi auth bootstraps easier to debug before launch.
When a local bash target overrides `HOME`, the inspect summary now also shows `Bootstrap home: ...` so the `startup=~/.profile -> ~/.bashrc` chain is grounded to the actual dotfiles directory AgentFlow will use at launch instead of your current shell home.
It also shows whether `agentflow run` or `agentflow smoke` will trigger the local doctor preflight automatically in the default `auto` mode, which helps you confirm bundled-smoke and Kimi-bootstrap detection before you launch anything.
When that auto preflight is enabled because of a local Kimi bootstrap, the inspect output now also names the matching nodes and whether the trigger came from `target.bootstrap`, `target.shell_init`, or `target.shell`, so it is easier to trust why the guard rail will run.
Use `--output json-summary` when you want the same compact information in a machine-readable format without the full prepared env and payload details from `--output json`.
For Kimi nodes, that inspect output also surfaces the effective default Moonshot provider even when you omit `provider:` from the pipeline, so the expected `KIMI_API_KEY` and base URL are visible before launch.
When a node launch will override current shell values such as `ANTHROPIC_BASE_URL` or `OPENAI_API_KEY`, that JSON summary also includes a structured `launch_env_overrides` list per node, including the override source, so wrappers can react without scraping warning text. Base-URL values are included verbatim; secret-like keys stay redacted.
That override summary also treats explicit empty-string base-URL values as intentional clears, so bundled smoke configs can show when they wipe an ambient relay such as `OPENAI_BASE_URL` before launch.
For local Claude-on-Kimi nodes in mixed-provider shells, that same summary now also includes `bootstrap_env_overrides` when the `kimi` helper will replace the current `ANTHROPIC_API_KEY` after the node switches away from another Anthropic-compatible base URL, so hidden auth shadowing is easier to spot before execution.
For local Codex or Claude nodes that still do not pin base-url routing explicitly, the same summary now also includes `launch_env_inheritances` when the launch will inherit an ambient `OPENAI_BASE_URL` or `ANTHROPIC_BASE_URL` from the current shell, even if you supplied a structured provider that sets auth but omits `base_url`, so hidden routing drift is visible before you run the DAG.

Run a pipeline once:

```bash
agentflow run examples/pipeline.yaml
```

On an interactive terminal, `agentflow run` now defaults to the same compact per-node summary that `smoke` uses; when stdout is redirected or piped, it still defaults to the full JSON run record so scripts do not break. You can always force either shape with `--output summary`, `--output json-summary`, or `--output json`.
When you use `agentflow run` with the bundled real-agent smoke file, an explicit reference to that bundled file, or a custom local pipeline that either bootstraps local Codex/Claude/Kimi nodes through `kimi`, runs local `kimi` nodes, or routes local Claude nodes through Kimi-compatible provider settings, AgentFlow now runs the same local preflight as `agentflow smoke` by default. Use `--preflight never` when you intentionally want to bypass those readiness checks.
Add `--show-preflight` when you want `run` to print the successful local preflight summary before execution starts. That summary is written to stderr so `--output json` and `--output json-summary` remain machine-readable on stdout, and it now also explains why auto preflight ran plus the matching node bootstrap sources when available.

Inspect how a pipeline will resolve prompts, shell bootstrap, and launch commands without executing any agents:

```bash
agentflow inspect examples/local-real-agents-kimi-smoke.yaml
```

Like `run` and `doctor`, `inspect` now auto-selects a terminal-friendly summary on TTY stdout and full JSON when stdout is redirected. Use `--output summary`, `--output json-summary`, or `--output json` when you want to force a specific shape. When a local node's bash login startup is broken in a way that blocks the Kimi helper or another login-shell bridge, `inspect` now prints the same ready-to-paste shell bridge snippet that `doctor` recommends, and the JSON forms expose it as a per-node `shell_bridge` object.

Run the bundled real-agent smoke check:

```bash
agentflow smoke
```

Run the same bundled local flow as a single readiness + execution check:

```bash
agentflow check-local
```

The bundled smoke now launches both `codex` and `claude` in parallel inside `bash -lic` so the default check covers scheduler fan-out as well as login-shell startup files for local CLI installs. The example also uses pipeline-level `local_target_defaults.bootstrap: kimi`, which keeps the default smoke aligned with shared Kimi bootstrap setups without repeating the same target block on every node.
The bundled Codex smoke node also clears any ambient `OPENAI_BASE_URL`, so a host-level relay or proxy does not silently hijack the default local smoke run.

`agentflow check-local` prints the Doctor report to stderr first and then launches the bundled smoke pipeline only when the local setup is ready enough to continue. Like `run`, it now defaults to the compact summary on a terminal and falls back to JSON when stdout is redirected or piped, so wrapper scripts can capture the run result without adding `--output json`. The repo-local `make check-local` shortcut uses that same single-pass CLI flow with `--output summary`, so maintainer runs stay compact even in non-TTY logs and you still do not pay for the Doctor preflight twice before the smoke run starts. `check-local` also accepts an optional custom pipeline path when you want the same doctor-then-run flow for another local Kimi-backed smoke DAG, and it now reuses that same validated pipeline snapshot for the launch step instead of reloading the file after Doctor succeeds. For wrapper parity with `run` and `smoke`, it also accepts `--show-preflight` plus `--preflight auto` or `--preflight always` without changing behavior; `check-local` still always runs Doctor first, so `--preflight never` remains invalid on purpose. When that custom path does not match the bundled smoke example and does not rely on a local `kimi` shell bootstrap, a local `kimi` node, or Kimi-compatible Claude provider routing, the Doctor step now skips the bundled smoke-only Kimi helper checks and sticks to the local agent checks that pipeline actually needs. When you pass `--output json` or `--output json-summary`, that preflight stderr payload is JSON so wrappers can parse readiness and run output separately.
For custom pipelines that still use a local `kimi` bootstrap, that preflight now keeps the lightweight `kimi_shell_helper` check but drops bundled smoke-only assumptions about default `codex` and `claude` commands, so custom executables and single-agent DAGs are not blocked by unrelated smoke prerequisites.
When those custom Kimi-backed preflights succeed, `doctor` and `check-local` also print the matching per-node local readiness probes such as `claude --version`, `codex --version`, and Codex auth readiness, so you can see that AgentFlow actually exercised the prepared shell bootstrap before the run starts. That `codex_auth` line now reports the effective source it found in the prepared shell, so it may name `OPENAI_API_KEY`, `codex login status`, or both when the prepared shell has an API key and a working Codex CLI login.

To verify the broader "real project in another directory" path with the same local Codex + Claude-on-Kimi bootstrap, run:

```bash
make check-local-custom
```

That helper writes the shared temporary local Codex + Claude-on-Kimi pipeline outside this repo and runs `agentflow check-local` against it, which makes it easier to catch regressions in custom-pipeline path resolution and shared Kimi bootstrap handling before they reach users.

When you want to verify the standalone local readiness path for that same kind of external Codex + Claude-on-Kimi pipeline, run:

```bash
make doctor-local-custom
```

That helper writes the same temporary pipeline outside this repo, runs `agentflow doctor --output summary`, and validates that the external pipeline still reports the expected bash startup, Kimi helper, Codex readiness/auth, and launch-env override details before any run starts.
Use `make doctor-local-custom-shell-init` when you want the same external verification against the explicit `shell: bash`, `shell_login: true`, `shell_interactive: true`, `shell_init: kimi` form instead of `local_target_defaults.bootstrap: kimi`.
Use `make doctor-local-custom-shell-wrapper` when you want the same external verification against the explicit `target.shell: "bash -lic 'command -v kimi >/dev/null 2>&1 && kimi && {command}'"` wrapper form.

When you want to verify the pre-launch inspection path for that same kind of external Codex + Claude-on-Kimi pipeline, run:

```bash
make inspect-local-custom
```

That helper writes the same temporary pipeline outside this repo, runs `agentflow inspect --output summary`, and validates that the reported working directory, per-node `Cwd`, Kimi bootstrap wiring, and prepared Codex/Claude launch summaries all resolve against the external pipeline path.
Use `make inspect-local-custom-shell-init` when you want the same contract verified for the explicit `shell_init: kimi` wiring instead of the preset bootstrap shorthand.
Use `make inspect-local-custom-shell-wrapper` when you want the same contract verified for the explicit `target.shell` Kimi wrapper.

When you want to exercise the main `agentflow run` path against that same kind of external Codex + Claude-on-Kimi pipeline, run:

```bash
make run-local-custom
```

That helper also writes the same temporary pipeline outside this repo, but it runs `agentflow run --output json-summary --show-preflight` and validates the wrapper contract: run JSON stays on stdout, the successful preflight summary stays on stderr, and both local agent nodes complete with the expected previews. If the live run fails, it now prints the captured stdout/stderr and keeps the temp directory path for debugging.
Use `make run-local-custom-shell-init` when you want the same stdout/stderr contract exercised for the explicit `shell_init: kimi` form too.
Use `make run-local-custom-shell-wrapper` when you want the same stdout/stderr contract exercised for the explicit `target.shell` Kimi wrapper too.

When you want the full maintainer smoke sequence in one command, run:

```bash
make verify-local
```

That wrapper now runs both the raw shell-level Kimi toolchain check and the bundled `agentflow toolchain-local`, `inspect`, `doctor`, and `check-local` commands before it moves on to the external custom-pipeline `doctor`, `inspect`, `check-local`, and `run` paths for `bootstrap: kimi`, explicit `shell_init: kimi`, and explicit `target.shell` Kimi wrappers. This keeps the maintainer path closer to the real user-facing debugging flow, so regressions in the bundled inspect or doctor surfaces do not hide behind a still-green `check-local` run. It also prints whether `~/.bash_profile`, `~/.bash_login`, or `~/.profile` is supplying the bash login startup path before it verifies `kimi`, `codex`, and `claude`, reports whether Codex auth is coming from `OPENAI_API_KEY`, Codex CLI login, or both in that shared Kimi-backed shell, and shows the resolved `codex` / `claude` executable paths so it is easier to catch mixed Node/npm installs.
Those maintainer scripts now apply the same timeout guard to every live `agentflow` invocation in the stack, including the external `doctor`, `inspect`, `check-local`, and `run` paths plus the bundled `check-local` step. Override that stack-wide budget with `AGENTFLOW_LOCAL_VERIFY_TIMEOUT_SECONDS`; when it is unset, the helper falls back to `AGENTFLOW_DOCTOR_TIMEOUT_SECONDS` if you already exported it, otherwise it uses a 60-second default that is large enough for the real local Codex plus Claude-on-Kimi runs.

By default, `agentflow smoke` now prints a compact per-node summary instead of the full run record JSON. Use `agentflow smoke --output json-summary` when you want a compact machine-readable payload for scripts, or `agentflow smoke --output json` when you want the complete persisted run record with stdout, stderr, and trace details.
Add `--show-preflight` when you want `smoke` to print the successful local readiness summary before the run starts. AgentFlow writes that extra summary to stderr so JSON stdout stays safe for wrappers and scripts, and it now includes the auto-preflight reason plus matched node bootstrap sources when available.

Manage persisted runs from the CLI without opening the web UI:

```bash
agentflow runs
agentflow show <run-id>
agentflow cancel <run-id>
agentflow rerun <run-id>
```

`agentflow runs` prints a compact summary of the most recent persisted runs under `--runs-dir` and defaults to the newest 20 entries so busy workspaces stay readable; pass `--limit 0` to show everything. `show` renders the same per-node summary for a single persisted run and still supports `--output json` or `--output json-summary`. `cancel` marks an active run as cancelling or cancels it immediately when it is still queued, and `rerun` re-executes the stored pipeline spec and waits for the fresh Codex/Claude/Kimi run to finish so the command behaves like a terminal-friendly replay.

The bundled smoke preflight now matches that output mode too, so warning and failure reports stay in summary form by default and switch to JSON when you pass `--output json`.
When those preflight checks detect a bash login startup bridge problem, the same smoke or run command now includes the ready-to-paste shell bridge recommendation inline instead of making you rerun Doctor separately.
When that preflight loads the bundled smoke pipeline, it now also applies the same per-node shell-bootstrap and local agent readiness checks that custom Kimi-backed pipelines already use, so edits to the bundled example are still validated before launch.
In the default `auto` mode, AgentFlow runs that preflight for the bundled smoke path, for explicit references to the bundled smoke file, and for custom local smoke pipelines that either bootstrap local Codex/Claude/Kimi nodes through `kimi`, run local `kimi` nodes, or route local Claude nodes through Kimi-compatible provider settings. `agentflow run` now uses the same guard rails for those same pipeline shapes. Use `--preflight always` to force preflight for other custom smoke pipelines too; AgentFlow still keeps those checks pipeline-specific instead of falling back to bundled smoke-only prerequisites. Use `--preflight never` when you need to skip preflight.

Check the local Codex/Claude/Kimi smoke prerequisites without launching a run:

```bash
agentflow doctor
```

`agentflow doctor` now defaults to `summary` when stdout is a terminal, which makes local readiness checks easier to scan. It still defaults to JSON when stdout is redirected or piped so CI and wrapper scripts can parse it directly, and you can always force either mode with `--output summary` or `--output json`. `doctor` also accepts `--output json-summary`, which emits the compact machine-readable view with per-status counts and concise checks while leaving the full per-check `context` payload on `--output json`. That keeps one summary-shaped flag working consistently across `inspect`, `doctor`, `run`, `smoke`, and `check-local`. When you pass a custom pipeline path that is not the bundled smoke example and does not use a local `kimi` shell bootstrap, `doctor` now skips the bundled smoke-only helper checks so Codex-only or Claude-only local DAGs are not blocked by unrelated Kimi prerequisites.
Plain `agentflow doctor` now also loads the bundled smoke pipeline and applies those same node-level shell/bootstrap checks, not just the shared host-level helper checks, so the default smoke example stays self-validating. It also reports the bundled run/smoke auto-preflight reason and matched nodes by default, so the no-argument maintainer path explains the same guard rail that `check-local`, `run`, and `smoke --show-preflight` already use.
Those JSON checks also include per-check `context` when AgentFlow can explain a machine-readable cause, such as the node id and resolved values behind a `launch_env_override` report.
For bash login startup checks, that same JSON context now includes the active login file, `startup_chain`, `startup_summary`, the bash login file presence matrix, and any `shadowed_startup_chain`, while the human summary appends the same startup chain inline as `startup=~/.profile -> ~/.bashrc` plus the current `~/.bash_profile` / `~/.bash_login` / `~/.profile` presence summary so `doctor`, `check-local`, and `make toolchain-local` stay easier to compare.
For the bundled smoke pipeline and custom local Kimi-bootstrapped Codex/Claude/Kimi DAGs, Doctor now also reports when the current shell exports conflicting launch values such as `ANTHROPIC_BASE_URL` that a node will replace at launch, and it carries the same source hint into the human summary and JSON context. Overrides that come directly from explicit pipeline configuration such as `node.env`, `provider.env`, or `provider.base_url` stay informational in Doctor so the preflight does not go yellow for an intentional mixed-provider setup.
In that same mixed-provider Claude-on-Kimi case, Doctor also emits a `bootstrap_env_override` info check when the local `kimi` helper will replace the current `ANTHROPIC_API_KEY` after the node switches to Kimi's Anthropic-compatible base URL.
When `node.env` or `provider.env` explicitly clears a required provider key such as `ANTHROPIC_API_KEY`, Doctor now treats that as a real preflight failure unless a later local bootstrap replaces it, which makes launch-time credential shadowing visible before the DAG starts.

You can also point Doctor at a custom pipeline to surface the same pipeline-specific local shell bootstrap warnings that `run` and `smoke` preflight use:

```bash
agentflow doctor path/to/pipeline.yaml --output summary
```

That keeps it easy to validate real Codex/Claude/Kimi DAGs before you launch them, especially when a node depends on a local `kimi` shell helper, the local Kimi bridge, or Kimi-compatible Claude routing. When you pass a pipeline path, Doctor now also reports whether `run` or `smoke` would auto-run the local preflight for that DAG and names the matching nodes and trigger sources, so you can confirm the guard rail from the same command. It also fails early when a pipeline node requires a provider API key such as `KIMI_API_KEY` or `ANTHROPIC_API_KEY` and that key is missing from the current environment, `node.env`, and `provider.env`, except when a local Claude-on-Kimi node already bootstraps those Anthropic credentials through `target.shell_init: kimi`, an equivalent `target.shell` wrapper, or an interactive/login Bash startup file reached by that wrapper. The same local-bootstrap shortcut applies to custom Claude provider objects when they still point at Kimi's Anthropic-compatible endpoint and use `ANTHROPIC_API_KEY`. For local Kimi nodes, Doctor now also probes the configured Python executable through the prepared local shell with `python -c 'import agentflow.remote.kimi_bridge'`, so broken wrappers and bad interpreter overrides fail before launch. That probe now uses the same default interpreter selection as a real Kimi run, including the repo-local `.venv/bin/python` fallback when the current Python is outside that virtualenv. For local Claude nodes that rely on the Kimi provider or a `kimi` shell bootstrap, Doctor now also runs `claude --version` through the node's prepared local shell bootstrap, so broken bash wrappers fail before launch instead of halfway through a run. For local Codex nodes, Doctor now also runs `codex --version` through the node's prepared local shell bootstrap, respecting `executable:` overrides so broken wrappers fail before launch, and it still checks `codex login status` through that same prepared shell whenever `OPENAI_API_KEY` is absent from the current environment, `node.env`, and `provider.env`. When a local Codex node already warns or fails because its `kimi` shell bootstrap is misconfigured, Doctor now reports that bootstrap issue once instead of stacking a second `codex_auth` failure on top. Direct local bootstrap exports such as `target.shell_init: export ANTHROPIC_API_KEY=...`, split assignment/export sequences like `ANTHROPIC_API_KEY=...` then `export ANTHROPIC_API_KEY`, sourced bootstrap files such as `source ~/.anthropic.env`, interactive Bash startup files reached through `target.shell`, and shell wrappers like `bash -lc 'export ANTHROPIC_API_KEY=... && {command}'` or `bash -lc 'source ~/.anthropic.env && {command}'` also satisfy that preflight, so you do not need to duplicate the same secret in YAML env blocks just to make Doctor happy.
Those local probes use a bounded timeout so broken shell wrappers or hung CLIs fail fast instead of hanging `doctor`, `check-local`, or auto-preflight forever. Override the default 15-second budget with `AGENTFLOW_DOCTOR_TIMEOUT_SECONDS` when a slower local bootstrap genuinely needs more time. The lighter-weight bash startup env probe that Doctor and `inspect` use to confirm credentials sourced from login or interactive Bash startup files also fails fast now; if that specific probe times out, Doctor reports it as a warning instead of a false missing-credentials failure, and you can raise its default 5-second budget with `AGENTFLOW_BASH_STARTUP_PROBE_TIMEOUT_SECONDS`.
Doctor also warns when a local Codex or Claude node would inherit an ambient `OPENAI_BASE_URL` or `ANTHROPIC_BASE_URL` from the current shell because the pipeline still does not pin that routing explicitly, including structured provider configs that set auth but omit `base_url`. That keeps shell-specific relay settings visible in preflight output instead of silently affecting the run.
Add `--shell-bridge` when you want Doctor to include a ready-to-paste login-shell bridge snippet for `~/.bash_profile`, `~/.bash_login`, or `~/.profile`.
Those Doctor and smoke/run preflight shell-bridge hints now also honor node-specific bash homes such as `target.shell: env HOME=/tmp/demo-home bash ...`, so the suggested bridge follows the login files that node will actually use instead of your current shell home.

Run the web console:

```bash
agentflow serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

When you validate or run inline YAML from the web console, the "Base dir for relative paths" field controls how relative `working_dir` and local `target.cwd` values resolve. It defaults to the server process working directory. API clients can send that same hint as `base_dir`, or skip inline YAML entirely and send `pipeline_path` to reuse the CLI's file-based path resolution.

## Runtime configuration

You can configure the runtime via CLI flags or environment variables.

- `AGENTFLOW_RUNS_DIR`: base directory for run state and artifacts
- `AGENTFLOW_MAX_CONCURRENT_RUNS`: maximum number of concurrently executing DAG runs

`AGENTFLOW_RUNS_DIR` and `--runs-dir` accept home-relative paths such as `~/.agentflow/runs`.

CLI equivalents:

```bash
agentflow serve --runs-dir .agentflow/runs --max-concurrent-runs 2
agentflow run examples/pipeline.yaml --runs-dir .agentflow/runs --max-concurrent-runs 2
agentflow run examples/pipeline.yaml --output summary
agentflow run examples/pipeline.yaml --output json-summary
```

## Airflow-like Python DAG

```python
from agentflow import DAG, claude, codex, kimi

with DAG("demo", working_dir=".", concurrency=3) as dag:
    plan = codex(task_id="plan", prompt="Inspect the repo and plan the work.")
    implement = claude(
        task_id="implement",
        prompt="Implement the plan:\n\n{{ nodes.plan.output }}",
        tools="read_write",
    )
    review = kimi(
        task_id="review",
        prompt="Review the plan:\n\n{{ nodes.plan.output }}",
        capture="trace",
    )
    merge = codex(
        task_id="merge",
        prompt="Merge the implementation and review outputs.",
    )

    plan >> [implement, review]
    [implement, review] >> merge

spec = dag.to_spec()
```

See `examples/airflow_like.py` for a complete runnable example.

## Pipeline schema

Each node supports:

- `agent`: `codex`, `claude`, or `kimi`
- `model`: any model string understood by the backend
- `provider`: a string or a structured provider config with `base_url`, `api_key_env`, headers, and env
- `tools`: `read_only` or `read_write`
- `mcps`: a list of MCP server definitions
- `skills`: a list of local skill paths or names
- `target`: `local`, `container`, or `aws_lambda`
- local working-dir and shell bootstrap fields: `cwd`, `bootstrap`, `shell`, `shell_login`, `shell_interactive`, and `shell_init`
- `capture`: `final` or `trace`
- `retries` and `retry_backoff_seconds`
- `success_criteria`: output or filesystem checks evaluated after execution

Skill entries are resolved from the pipeline `working_dir`. You can point `skills:` at a plain file, a `.md` file, a home-relative path such as `~/.codex/skills/release-skill`, or a directory that contains `SKILL.md`, which keeps reusable local skill bundles easy to share across Codex, Claude, and Kimi nodes.

Top-level pipeline controls include:

- `concurrency`: max parallel nodes within a run
- `fail_fast`: skip downstream work after the first failed node

Runtime numeric settings are validated up front: `concurrency` must be at least `1`, `timeout_seconds` must be greater than `0`, and both `retries` and `retry_backoff_seconds` must be non-negative.

MCP definitions are also validated before launch: `stdio` servers require `command` and reject HTTP-only fields such as `url`, `streamable_http` servers require `url` and reject stdio-only fields such as `command`, and MCP server names must be unique within a node.

Built-in provider shorthands:

- `codex`: `openai`
- `claude`: `anthropic`, `kimi`
- `kimi`: `kimi`, `moonshot`, `moonshot-ai`

`provider: kimi` is intentionally rejected on `codex` nodes. Codex requires an OpenAI Responses API backend, and Kimi's public endpoints do not expose `/responses`.

When both `provider.env` and `node.env` define the same variable, `node.env` wins. That keeps per-node overrides aligned with the values `agentflow doctor`, `inspect`, and the actual launch environment use.
For Claude-compatible Kimi setups, that same effective-env rule also means `doctor` and `inspect` recognize custom providers that set `ANTHROPIC_BASE_URL=https://api.kimi.com/coding/` in `provider.env`, even when `provider.base_url` is omitted.

## Execution targets

### Local

Runs the prepared agent command directly on the host. Set `target.shell` to wrap the command in a specific shell, such as `bash -lc`. If you provide a shell name without an explicit command flag, AgentFlow uses `-c` by default; opt into startup file loading with `shell_login: true` and `shell_interactive: true`. You can also use a `{command}` placeholder in the shell string to run shell bootstrap steps before the prepared agent command.

That default `-c` behavior applies to `{command}` templates too, so wrappers such as `env FOO=bar bash {command}` work without needing to spell `-c` manually.
When the wrapper starts with `env -i`, AgentFlow now preserves the prepared launch env by inlining those variables into the `env` command itself, and it still applies `shell_login` / `shell_interactive` to the real shell executable rather than mistaking wrapper flags such as `env -i` for shell flags.
For Bash specifically, use `-c` and either `-i` or `shell_interactive: true`; Bash accepts `--login`, but not `--command` or `--interactive`, and AgentFlow now rejects those invalid wrappers during validation instead of failing later at launch time.

`target.cwd` controls the local node working directory. Absolute paths are used as-is; relative paths are resolved from the pipeline `working_dir`. File-based success criteria such as `file_exists`, `file_contains`, and `file_nonempty` are evaluated from that resolved local node working directory.

The local-shell bootstrap fields `shell_login`, `shell_interactive`, and `shell_init` require `target.shell`. For the common Kimi helper case, `target.bootstrap: kimi` expands to the same `bash` + login + interactive + `shell_init` setup automatically.

For common shell helper workflows, you can keep the config declarative instead of hand-writing a quoted shell template:

```yaml
target:
  kind: local
  bootstrap: kimi
```

This expands to `shell: bash`, `shell_login: true`, `shell_interactive: true`, and `shell_init: ["command -v kimi >/dev/null 2>&1", "kimi"]`, then launches the prepared agent command. `shell_init` still accepts either a single command or a list of commands when you need the lower-level form; list entries are joined with `&&` so bootstrap failures still stop the wrapped agent launch. When you combine `bootstrap: kimi` with extra `shell_init` commands, AgentFlow now keeps those extra commands first and still appends the Kimi helper automatically, so node-specific bootstrap tweaks do not silently drop the shared Kimi setup.
`agentflow validate` also treats `bootstrap: kimi` as a contract: if later overrides switch the node away from bash or disable interactive bash startup, validation now fails early instead of deferring that contradiction to inspect or preflight. If you need a different shell shape, drop `target.bootstrap` and configure the wrapper explicitly.

When most local nodes share the same shell bootstrap, move that block to top-level `local_target_defaults` and only override the nodes that differ:

```yaml
local_target_defaults:
  bootstrap: kimi

nodes:
  - id: codex_plan
    agent: codex
    prompt: Reply with exactly: codex ok

  - id: claude_review
    agent: claude
    provider: kimi
    prompt: Reply with exactly: claude ok
    target:
      cwd: review
```

AgentFlow applies `local_target_defaults` to local nodes that omit `target`, merges it into local nodes that only override part of `target`, and leaves container or Lambda targets unchanged. If you prefer to spell the wrapper directly, explicit shells such as `bash -lic` behave the same way, and AgentFlow suppresses Bash's harmless no-job-control stderr noise for those interactive wrappers too. If your login shell uses `~/.bash_profile`, make sure it eventually reaches `~/.bashrc`, either directly or via another startup file such as `~/.profile`; otherwise Bash only reads `~/.profile` when no `~/.bash_profile` or `~/.bash_login` file is present. A minimal bridge looks like:

If one local node should not inherit the shared Kimi preset, set `target.bootstrap: null` on that node. AgentFlow now treats that as an explicit opt-out from the inherited bootstrap while still preserving unrelated local defaults such as `cwd`. That opt-out also drops inherited shell bootstrap fields such as `shell`, `shell_login`, `shell_interactive`, and any shared `shell_init` commands, including extra commands that were prepended to the shared Kimi bootstrap.

```yaml
local_target_defaults:
  bootstrap: kimi
  cwd: workspace

nodes:
  - id: codex_plan
    agent: codex
    prompt: Reply with exactly: codex ok

  - id: codex_direct
    agent: codex
    prompt: Reply with exactly: direct ok
    target:
      bootstrap: null
```

```bash
# ~/.bash_profile or ~/.profile
if [ -f "$HOME"/.bashrc ]; then
  . "$HOME"/.bashrc
fi
```

When you bootstrap `kimi` through Bash, keep Bash interactive too. `agentflow inspect` and the `run`/`smoke` auto-preflight now warn when either `shell_init: kimi` or an explicit `target.shell` wrapper such as `bash -lc 'kimi && {command}'`, `bash -lc 'eval "$(kimi)" && {command}'`, `bash -lc 'export $(kimi) && {command}'`, or `bash -lc 'source <(kimi) && {command}'` is paired with non-interactive Bash, because helpers defined in `~/.bashrc` are commonly skipped in `bash -lc`-style setups. That warning also calls out the easy-to-miss case where the wrapper explicitly runs `source ~/.bashrc` first, but `~/.bashrc` still returns early for non-interactive shells on the current host. If you intentionally preload `kimi` through `BASH_ENV=/path/to/shell.env bash -c ...` and that file defines the `kimi` helper or sources another file that does, AgentFlow treats that wrapper as ready and does not emit the `~/.bashrc` warning. The same readiness shortcut now applies when a non-interactive Bash wrapper explicitly sources a login file such as `~/.profile` or `~/.bash_profile` before `kimi`, as long as that sourced file really defines `kimi` or reaches another file that does. Use an explicit home path such as `~/.profile` or `$HOME/.bashrc` for those bridges; bare relative paths like `.bashrc` resolve from the shell's current working directory, not your home directory.

`agentflow doctor` now also calls out the easy-to-miss case where `~/.bash_profile` exists, does not source `~/.bashrc`, and silently prevents a working `~/.profile` -> `~/.bashrc` bridge from ever running. When Bash falls back to `~/.profile` because `~/.bash_profile` and `~/.bash_login` are absent, the doctor output now says so directly. It also recognizes bridges written as absolute home paths such as `source /home/alice/.bashrc`, treats `~/.bashrc` symlinks into an external dotfiles repo as valid login-shell bridges, follows custom sourced files inside `HOME` such as `~/.bash_profile` -> `~/.bash_agentflow` -> `~/.bashrc`, verifies that both `claude --version` and `codex --version` still work after the shared `kimi` bootstrap, and reports unreadable startup files as warnings instead of crashing. When bash startup checks fail in less predictable ways, the doctor output now strips Bash's harmless interactive-shell noise and redacts likely secret values before echoing shell diagnostics back to you. If you want the exact bridge to paste into the active login file, run `agentflow doctor --output summary --shell-bridge`.

`shell_init` is treated as a bootstrap prerequisite: if it exits non-zero, AgentFlow does not launch the wrapped agent command. This helps smoke runs fail fast when helper functions such as `kimi` are missing.

### Container

Wraps the command in `docker run`, mounts the working directory, runtime directory, and the AgentFlow app, then streams stdout and stderr back into the run trace.

### AWS Lambda

Invokes `agentflow.remote.lambda_handler.handler`. The payload contains the prepared command, environment, runtime files, and execution metadata so the Lambda package can execute the node remotely.

## Agent notes

### Codex

- Uses `codex exec --json`
- Maps tools mode to Codex sandboxing
- Writes `CODEX_HOME/config.toml` per node for provider and MCP selection

### Claude

- Uses `claude -p ... --output-format stream-json --verbose`
- Passes `--tools` according to the read-only vs read-write policy
- Writes a per-node MCP JSON config and passes it with `--mcp-config`

### Kimi

- Uses the active Python interpreter via `sys.executable -m agentflow.remote.kimi_bridge`
- Emits a Kimi-style JSON-RPC event stream
- Calls Moonshot's OpenAI-compatible chat completions API
- Provides a small built-in tool layer for read, search, write, and shell actions

## Web console

The frontend shows:

- current DAG state and node statuses
- live run timeline and parsed JSONL trace events
- per-node attempts and retry history
- final outputs plus launch, stdout, stderr, trace, and result artifacts
- controls to validate, launch, cancel, and rerun pipelines

Artifact files are persisted under `AGENTFLOW_RUNS_DIR/<run_id>/artifacts/<node_id>/`.
Each node now includes a redacted `launch.json` artifact that records the resolved command, working directory, selected runtime files, and any remote-runner payload metadata without storing secret env values, inline shell bootstrap assignments, or runtime file contents.
When a node retries, AgentFlow also keeps `launch-attempt-<n>.json` snapshots for each attempt so you can compare bootstrap, command, and payload changes without losing the final `launch.json` view that the web console and API already use.
For structured agent streams such as Codex and Claude JSON output, `stdout.log` keeps the full raw stream while `result.json` and downstream node context omit parser-ignored control chatter such as startup hooks and suppressed warnings.

## API surface

The FastAPI app exposes:

- `POST /api/runs/validate`
- `POST /api/runs`
- `GET /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/events`
- `GET /api/runs/{run_id}/stream`
- `GET /api/runs/{run_id}/artifacts/{node_id}/{name}`
- `POST /api/runs/{run_id}/cancel`
- `POST /api/runs/{run_id}/rerun`
- `GET /api/health`

## Testing

Run the Python suite:

```bash
make test
```

That shortcut uses the same interpreter resolution as the other repo-local helpers: `.venv/bin/python` when the repo virtualenv exists, otherwise `python3`. Run `make python` when you want to confirm which interpreter those shortcuts will use on your machine.

Run the browser suite:

```bash
npm install
npx playwright install chromium
npx playwright test
```

The Playwright tests use lightweight mock `codex` and `claude` executables under `tests/e2e/bin/` and exercise validation, retries, rerun, cancellation, and artifact viewing through the web UI.

Run a real local smoke check with your installed CLIs:

```bash
. .venv/bin/activate
agentflow smoke
```

When you are iterating on local Kimi shell bootstrap behavior itself, run the lighter maintainer check first:

```bash
agentflow toolchain-local
```

That command intentionally uses `bash -lic` before running `kimi`, `codex --version`, and `claude --version`, so it exercises the same login + interactive shell path that the bundled local smoke depends on. This avoids the easy-to-misread non-interactive `source ~/.bashrc` path where Bash often returns before the `kimi` helper is defined.
It also now enforces the same Kimi-specific assumptions that the bundled smoke preflight depends on: `kimi` must export `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` must resolve to `https://api.kimi.com/coding/`, and Codex auth must already work via `codex login status` or `OPENAI_API_KEY`.
Before those checks run, `agentflow toolchain-local` also prints the active bash login startup chain, for example `~/.bash_profile -> ~/.profile -> ~/.bashrc` or `~/.profile -> ~/.bashrc`, so it is easier to confirm which file actually supplied the shared Kimi bootstrap on your machine. The summary now also includes the resolved `codex` and `claude` executable paths alongside their versions, which helps when the local shell and the prepared smoke shell are not using the same install.
Like the other maintainer-oriented CLI surfaces, `toolchain-local` also accepts `--output json-summary` when you want a compact machine-readable payload for wrappers without the full `shell_bridge.snippet` from `--output json`.
On hosts that rely on Bash falling back to `~/.profile`, a healthy setup can still print `~/.bash_profile: missing` and `~/.bash_login: missing`; the key line is the resolved `bash login startup: ...` chain. When that chain is broken or shadowed, the maintainer check follows it with either `bash login bridge: not needed` or the explicit target, source, reason, and snippet to paste into the active login file.
Those maintainer shell probes fail fast too: the default budget is 15 seconds per wrapped command, and `AGENTFLOW_DOCTOR_TIMEOUT_SECONDS` raises that budget when a slower local bootstrap genuinely needs more time.

From the repo root, `make toolchain-local` is now just a thin shortcut for `agentflow toolchain-local --output summary`.

From the repo root, `make inspect-local`, `make doctor-local`, `make smoke-local`, and `make check-local` wrap the same bundled Kimi-backed workflow and now prefer `.venv/bin/python` automatically when that repo-local virtualenv exists, falling back to `python3` otherwise. `make check-local` now delegates straight to `agentflow check-local --output summary`, which keeps the preflight and run in one pass instead of rerunning Doctor through `smoke-local`, while forcing compact maintainer-friendly output even when logs are captured without a TTY. The CLI itself still keeps its normal auto-output behavior outside that shortcut, and its stderr preflight report follows the requested run output style: summary for `--output summary`, full JSON for `--output json`, and compact JSON summary for `--output json-summary`. When you specifically want the external custom-pipeline versions of the doctor, inspect, check-local, and run contracts, the `make *-local-custom*` helpers now cover `bootstrap: kimi`, explicit `shell_init: kimi`, and explicit `target.shell` Kimi wrappers against the real local Codex and Claude CLIs.

This keeps the check small while exercising both local `codex` and local `claude` end-to-end. Before the bundled smoke pipeline starts, AgentFlow runs a local preflight that verifies `codex`, confirms that `bash -lic` can find the `kimi` shell helper and still launch both `claude` and `codex` afterwards, checks that `claude --version` still works inside that shared smoke shell, checks that `kimi` exports both `ANTHROPIC_API_KEY` and the Kimi Claude endpoint in `ANTHROPIC_BASE_URL`, confirms Codex authentication is ready inside that shared smoke shell via `codex login status` or `OPENAI_API_KEY`, and reports which bash login startup file is active, including transitive bridges such as `~/.bash_profile` -> `~/.profile` -> `~/.bashrc`. That startup check also accepts the common dotfiles pattern where `~/.bashrc` itself is a symlink into another repo. The preflight warns when a login startup file references `~/.bashrc` but that file is missing, or when no bash login startup file exists to bridge into `~/.bashrc` at all. If `codex` or `claude` only become available inside that shared login-shell bootstrap, the readiness report still stays green because the bundled smoke pipeline can already launch them there.

When `~/.bash_profile` or `~/.bash_login` shadows a working `~/.profile` bridge, the preflight now tells you that the alternate startup path will never run, points you at the file that needs the bridge, and includes the bridge snippet inline when one is available.

You can run the same preflight directly:

```bash
. .venv/bin/activate
agentflow doctor
```

When `codex` or `claude` are already on `PATH`, the doctor summary now includes the resolved executable path and `--version` output to make local CLI mismatches easier to spot. Use `agentflow doctor --output json-summary` when you want the compact machine-readable summary payload that matches the other orchestration commands, or `--output json` when you need each check's full `context`.

The bundled smoke pipeline bootstraps the `kimi` shell helper inside both local nodes, so you do not need to wrap the entire `agentflow smoke` command in `bash -lic`. If you want to run a custom smoke pipeline instead, pass its path explicitly with `agentflow smoke path/to/pipeline.yaml`, or run it directly with `agentflow run path/to/pipeline.yaml` and keep the same `auto` preflight behavior for bundled and Kimi-bootstrapped local smoke pipelines. `run` now mirrors `smoke` by defaulting to the compact summary on an interactive terminal while still falling back to full JSON when stdout is redirected. Use `--preflight always` for other custom pipelines that still need those checks; forced preflight still follows the agents and bootstrap that your pipeline actually uses. Use `--preflight never` to skip preflight even for the bundled example. Add `--output json-summary` when you want a concise machine-readable result, or `--output json` when you want the full persisted run record instead of the compact summary.

## Reference sources

- `https://developers.openai.com/codex/security`
- `https://docs.anthropic.com/en/docs/claude-code/sdk`
- `https://github.com/openai/codex`
- `https://github.com/RichardAtCT/claude-code-telegram`
- `https://github.com/MoonshotAI/kimi-cli`
