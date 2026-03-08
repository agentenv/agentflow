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

## Quick start

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
Those auth hints call out whether a node will rely on `node.env`, `provider.env`, the current environment, local shell bootstrap such as `target.shell_init: kimi`, or Codex CLI login fallback, so it is easier to spot hidden local prerequisites before launch.
When a local Claude node relies on Kimi's Anthropic-compatible bootstrap, the auth hint now keeps the Kimi helper first even if `ANTHROPIC_API_KEY` is already present in `node.env`, `provider.env`, or the current environment, because that helper runs last in the prepared shell and becomes the effective launch source.
For local Codex nodes that run through a `kimi` shell bootstrap, the same auth summary now also calls out that bootstrap even when `OPENAI_API_KEY` already comes from the environment, and it names that bootstrap as the Codex CLI login path when no key is injected.
When a resolved provider also needs to override conflicting shell values such as `ANTHROPIC_BASE_URL`, `inspect` now warns that the node launch env will replace the current value and names whether that override came from `node.env`, `provider.env`, or provider-derived settings such as `provider.base_url`, which makes mixed provider shells easier to debug before launch.
For local nodes, it also surfaces shell bootstrap details such as `shell`, login and interactive flags, and `shell_init`, so Kimi-backed wrappers are easier to confirm without decoding the full launch command. Inline secret assignments in `shell_init` or shell wrappers are redacted in both `inspect` output and persisted `launch.json` artifacts.
It also shows whether `agentflow run` or `agentflow smoke` will trigger the local doctor preflight automatically in the default `auto` mode, which helps you confirm bundled-smoke and Kimi-bootstrap detection before you launch anything.
When that auto preflight is enabled because of a local Kimi bootstrap, the inspect output now also names the matching nodes and whether the trigger came from `target.shell_init` or `target.shell`, so it is easier to trust why the guard rail will run.
Use `--output json-summary` when you want the same compact information in a machine-readable format without the full prepared env and payload details from `--output json`.
When a node launch will override current shell values such as `ANTHROPIC_BASE_URL` or `OPENAI_API_KEY`, that JSON summary also includes a structured `launch_env_overrides` list per node, including the override source, so wrappers can react without scraping warning text. Base-URL values are included verbatim; secret-like keys stay redacted.

Run a pipeline once:

```bash
agentflow run examples/pipeline.yaml
```

When you use `agentflow run` with the bundled real-agent smoke file, an explicit reference to that bundled file, or a custom local pipeline that clearly bootstraps local Codex, Claude, or Kimi nodes through `kimi`, AgentFlow now runs the same local preflight as `agentflow smoke` by default. Use `--preflight never` when you intentionally want to bypass those readiness checks.
Add `--show-preflight` when you want `run` to print the successful local preflight summary before execution starts. That summary is written to stderr so `--output json` and `--output json-summary` remain machine-readable on stdout.

Inspect how a pipeline will resolve prompts, shell bootstrap, and launch commands without executing any agents:

```bash
agentflow inspect examples/local-real-agents-kimi-smoke.yaml
```

Run the bundled real-agent smoke check:

```bash
agentflow smoke
```

The bundled smoke now launches both `codex` and `claude` inside `bash -lic` so login-shell startup files are exercised for local CLI installs. Both nodes also run `kimi` first, which keeps the default smoke aligned with shared Kimi bootstrap setups where the same shell helper prepares both CLIs.

By default, `agentflow smoke` now prints a compact per-node summary instead of the full run record JSON. Use `agentflow smoke --output json-summary` when you want a compact machine-readable payload for scripts, or `agentflow smoke --output json` when you want the complete persisted run record with stdout, stderr, and trace details.
Add `--show-preflight` when you want `smoke` to print the successful local readiness summary before the run starts. AgentFlow writes that extra summary to stderr so JSON stdout stays safe for wrappers and scripts.

The bundled smoke preflight now matches that output mode too, so warning and failure reports stay in summary form by default and switch to JSON when you pass `--output json`.
When those preflight checks detect a bash login startup bridge problem, the same smoke or run command now includes the ready-to-paste shell bridge recommendation inline instead of making you rerun Doctor separately.
In the default `auto` mode, AgentFlow runs that preflight for the bundled smoke path, for explicit references to the bundled smoke file, and for custom local smoke pipelines that clearly bootstrap local Codex, Claude, or Kimi nodes through `kimi` in their local shell target. `agentflow run` now uses the same guard rails for those same pipeline shapes. Use `--preflight always` to force the same checks for other custom smoke pipelines, or `--preflight never` when you need to skip them.

Check the local Codex/Claude/Kimi smoke prerequisites without launching a run:

```bash
agentflow doctor
```

`agentflow doctor` prints JSON by default so CI and wrapper scripts can parse it directly. Use `agentflow doctor --output summary` when you want a quick human-readable checklist instead.
Those JSON checks also include per-check `context` when AgentFlow can explain a machine-readable cause, such as the node id and resolved values behind a `launch_env_override` warning.
For the bundled smoke pipeline and custom local Kimi-bootstrapped Codex/Claude/Kimi DAGs, Doctor now also warns when the current shell exports conflicting launch values such as `ANTHROPIC_BASE_URL` that a node will replace at launch, and it carries the same source hint into the human summary and JSON context.

You can also point Doctor at a custom pipeline to surface the same pipeline-specific local shell bootstrap warnings that `run` and `smoke` preflight use:

```bash
agentflow doctor path/to/pipeline.yaml --output summary
```

That keeps it easy to validate real Codex/Claude/Kimi DAGs before you launch them, especially when a node depends on a local `kimi` shell helper or other bash startup behavior. When you pass a pipeline path, Doctor now also reports whether `run` or `smoke` would auto-run the local preflight for that DAG and names the matching nodes and trigger sources, so you can confirm the guard rail from the same command. It also fails early when a pipeline node requires a provider API key such as `KIMI_API_KEY` or `ANTHROPIC_API_KEY` and that key is missing from the current environment, `node.env`, and `provider.env`, except when a local Claude-on-Kimi node already bootstraps those Anthropic credentials through `target.shell_init: kimi` or an equivalent `target.shell` wrapper. The same local-bootstrap shortcut applies to custom Claude provider objects when they still point at Kimi's Anthropic-compatible endpoint and use `ANTHROPIC_API_KEY`. For local Kimi nodes, Doctor now also probes the configured Python executable through the prepared local shell with `python -c 'import agentflow.remote.kimi_bridge'`, so broken wrappers and bad interpreter overrides fail before launch. For local Claude nodes that rely on the Kimi provider or a `kimi` shell bootstrap, Doctor now also runs `claude --version` through the node's prepared local shell bootstrap, so broken bash wrappers fail before launch instead of halfway through a run. For local Codex nodes, Doctor also checks `codex login status` through the node's prepared local shell bootstrap whenever `OPENAI_API_KEY` is absent from those same env sources, so shared Kimi-backed wrappers fail early in the same context they would actually launch. Direct local bootstrap exports such as `target.shell_init: export ANTHROPIC_API_KEY=...`, split assignment/export sequences like `ANTHROPIC_API_KEY=...` then `export ANTHROPIC_API_KEY`, and shell wrappers like `bash -lc 'export ANTHROPIC_API_KEY=... && {command}'` also satisfy that preflight, so you do not need to duplicate the same secret in YAML env blocks just to make Doctor happy.
Add `--shell-bridge` when you want Doctor to include a ready-to-paste login-shell bridge snippet for `~/.bash_profile`, `~/.bash_login`, or `~/.profile`.

Run the web console:

```bash
agentflow serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

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
- local working-dir and shell bootstrap fields: `cwd`, `shell`, `shell_login`, `shell_interactive`, and `shell_init`
- `capture`: `final` or `trace`
- `retries` and `retry_backoff_seconds`
- `success_criteria`: output or filesystem checks evaluated after execution

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
For Bash specifically, use `-c` and either `-i` or `shell_interactive: true`; Bash accepts `--login`, but not `--command` or `--interactive`, and AgentFlow now rejects those invalid wrappers during validation instead of failing later at launch time.

`target.cwd` controls the local node working directory. Absolute paths are used as-is; relative paths are resolved from the pipeline `working_dir`. File-based success criteria such as `file_exists`, `file_contains`, and `file_nonempty` are evaluated from that resolved local node working directory.

The local-shell bootstrap fields `shell_login`, `shell_interactive`, and `shell_init` require `target.shell`. AgentFlow now rejects configs that set those fields without an explicit shell, because they would otherwise be silently ignored.

For common shell helper workflows, you can keep the config declarative instead of hand-writing a quoted shell template:

```yaml
target:
  kind: local
  shell: bash
  shell_login: true
  shell_interactive: true
  shell_init:
    - command -v kimi >/dev/null 2>&1
    - kimi
```

This runs the node inside `bash`, explicitly enables login and interactive startup files, verifies `kimi` is available, executes it, and then launches the prepared agent command. `shell_init` accepts either a single command or a list of commands; list entries are joined with `&&` so bootstrap failures still stop the wrapped agent launch. It is useful for helper functions defined in `~/.bashrc` without forcing you to hand-write quoted shell templates. If you prefer to spell the wrapper directly, explicit shells such as `bash -lic` behave the same way, and AgentFlow suppresses Bash's harmless no-job-control stderr noise for those interactive wrappers too. If your login shell uses `~/.bash_profile`, make sure it eventually reaches `~/.bashrc`, either directly or via another startup file such as `~/.profile`; otherwise Bash only reads `~/.profile` when no `~/.bash_profile` or `~/.bash_login` file is present. A minimal bridge looks like:

```bash
# ~/.bash_profile or ~/.profile
if [ -f "$HOME"/.bashrc ]; then
  . "$HOME"/.bashrc
fi
```

When you bootstrap `kimi` through Bash, keep Bash interactive too. `agentflow inspect` and the `run`/`smoke` auto-preflight now warn when either `shell_init: kimi` or an explicit `target.shell` wrapper such as `bash -lc 'kimi && {command}'`, `bash -lc 'eval "$(kimi)" && {command}'`, or `bash -lc 'source <(kimi) && {command}'` is paired with non-interactive Bash, because helpers defined in `~/.bashrc` are commonly skipped in `bash -lc`-style setups. That warning also calls out the easy-to-miss case where the wrapper explicitly runs `source ~/.bashrc` first, but `~/.bashrc` still returns early for non-interactive shells on the current host. If you intentionally preload `kimi` through `BASH_ENV=/path/to/shell.env bash -c ...` and that file defines the `kimi` helper, AgentFlow treats that wrapper as ready and does not emit the `~/.bashrc` warning.

`agentflow doctor` now also calls out the easy-to-miss case where `~/.bash_profile` exists, does not source `~/.bashrc`, and silently prevents a working `~/.profile` -> `~/.bashrc` bridge from ever running. When Bash falls back to `~/.profile` because `~/.bash_profile` and `~/.bash_login` are absent, the doctor output now says so directly. It also recognizes bridges written as absolute home paths such as `source /home/alice/.bashrc`, treats `~/.bashrc` symlinks into an external dotfiles repo as valid login-shell bridges, follows custom sourced files inside `HOME` such as `~/.bash_profile` -> `~/.bash_agentflow` -> `~/.bashrc`, verifies that `claude --version` still works after the shared `kimi` bootstrap, and reports unreadable startup files as warnings instead of crashing. When bash startup checks fail in less predictable ways, the doctor output now strips Bash's harmless interactive-shell noise and redacts likely secret values before echoing shell diagnostics back to you. If you want the exact bridge to paste into the active login file, run `agentflow doctor --output summary --shell-bridge`.

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
. .venv/bin/activate
pytest -q
```

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

This keeps the check small while exercising both local `codex` and local `claude` end-to-end. Before the bundled smoke pipeline starts, AgentFlow runs a local preflight that verifies `codex`, confirms that `bash -lic` can find the `kimi` shell helper and still launch both `claude` and `codex` afterwards, checks that `claude --version` still works inside that shared smoke shell, checks that `kimi` exports both `ANTHROPIC_API_KEY` and the Kimi Claude endpoint in `ANTHROPIC_BASE_URL`, confirms Codex authentication is ready inside that shared smoke shell via `codex login status` or `OPENAI_API_KEY`, and reports which bash login startup file is active, including transitive bridges such as `~/.bash_profile` -> `~/.profile` -> `~/.bashrc`. That startup check also accepts the common dotfiles pattern where `~/.bashrc` itself is a symlink into another repo. The preflight warns when a login startup file references `~/.bashrc` but that file is missing, or when no bash login startup file exists to bridge into `~/.bashrc` at all. If `codex` or `claude` only become available inside that shared login-shell bootstrap, the readiness report still stays green because the bundled smoke pipeline can already launch them there.

When `~/.bash_profile` or `~/.bash_login` shadows a working `~/.profile` bridge, the preflight now tells you that the alternate startup path will never run, points you at the file that needs the bridge, and includes the bridge snippet inline when one is available.

You can run the same preflight directly:

```bash
. .venv/bin/activate
agentflow doctor
```

The bundled smoke pipeline bootstraps the `kimi` shell helper inside the Claude node, so you do not need to wrap the entire `agentflow smoke` command in `bash -lic`. If you want to run a custom smoke pipeline instead, pass its path explicitly with `agentflow smoke path/to/pipeline.yaml`, or run it directly with `agentflow run path/to/pipeline.yaml` and keep the same `auto` preflight behavior for bundled and Kimi-bootstrapped local smoke pipelines. Use `--preflight always` for other custom pipelines that still need those checks, or `--preflight never` to skip preflight even for the bundled example. Add `--output json-summary` when you want a concise machine-readable result, or `--output json` when you want the full persisted run record instead of the compact summary.

## Reference sources

- `https://developers.openai.com/codex/security`
- `https://docs.anthropic.com/en/docs/claude-code/sdk`
- `https://github.com/openai/codex`
- `https://github.com/RichardAtCT/claude-code-telegram`
- `https://github.com/MoonshotAI/kimi-cli`
