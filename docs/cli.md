# CLI and Operations

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

Run the CLI as `agentflow ...` or `python -m agentflow ...`.

## Templates

List bundled starters:

```bash
agentflow templates
```

Scaffold a starter:

```bash
agentflow init > pipeline.yaml
agentflow init repo-sweep.yaml --template codex-fanout-repo-sweep
agentflow init repo-sweep-batched.yaml --template codex-repo-sweep-batched
agentflow init kimi-smoke.yaml --template local-kimi-smoke
```

The bundled templates are:

- `pipeline`
- `codex-fanout-repo-sweep`
- `codex-repo-sweep-batched`
- `local-kimi-smoke`
- `local-kimi-shell-init-smoke`
- `local-kimi-shell-wrapper-smoke`

## Validate and Inspect

Validate a pipeline:

```bash
agentflow validate examples/pipeline.yaml
```

Inspect the resolved launch plan:

```bash
agentflow inspect examples/pipeline.yaml
agentflow inspect examples/codex-repo-sweep-batched.yaml --output summary
```

## Run

Run a pipeline once:

```bash
agentflow run examples/pipeline.yaml
```

On a terminal, `run` and `inspect` default to a compact summary. When stdout is redirected, they fall back to JSON-oriented output. You can always force a format with `--output`.

## Serve the local web UI

Start the local web UI and API:

```bash
agentflow serve
```

Defaults:
- host: `127.0.0.1`
- port: `8000`

The web API only accepts `application/json` for `/api/runs` and `/api/runs/validate`.

For safety, the browser-facing API also disables `pipeline_path` by default, so a request cannot cause AgentFlow to execute a local `.py` pipeline file just by naming its path.

If you intentionally want to allow filesystem path loading from the local web API in a trusted environment, opt in explicitly:

```bash
AGENTFLOW_API_ALLOW_PIPELINE_PATH=1 agentflow serve
```

Treat that override as a trusted operator-only setting.

## Tuned Agents And Evolution

PR #11 adds a local tuned-agent workflow:

1. Run a pipeline that contains at least one `codex` node and completes with trace artifacts under `.agentflow/runs/<run_id>/artifacts/<node_id>/trace.jsonl`.
2. Evolve a tuned agent from that run:

```bash
python -m agentflow evolve <run_id> -n <node_id> --target codex --profile codex --optimizer codex
```

3. Inspect the local tuned-agent registry:

```bash
python -m agentflow tuned-agents
python -m agentflow tuned-agent codex_tuned --output json
```

The default Codex profile lives at `agent_tuner/codex.yaml`. It clones `https://github.com/openai/codex.git`, applies the optimizer agent to the cloned repo, then runs:

- `cargo build -p codex-cli`
- `cargo test -p codex-cli --lib && cargo test -p codex-models-manager --lib && cargo test -p codex-tools`
- `{executable} --help >/dev/null`

Generated versions are stored under `.agentflow/tuned_agents/<name>/versions/<version>/`.

### Requirements

- The pipeline `working_dir` used for the source run must point at the workspace that contains `agent_tuner/` and `.agentflow/`.
- The source run must include Codex trace artifacts.
- The local machine must be able to clone the profile `repo_url`.
- The local machine must have the build toolchain required by the profile. The bundled Codex profile requires Rust and `cargo`.

### Local Target Limitation

Tuned agents currently resolve only for local targets. If a node uses `agent: codex_tuned`, its execution target must remain `local`.

### External Sandbox Note

If Codex itself is running inside an externally sandboxed environment and its own shell sandbox fails to start, set:

```bash
AGENTFLOW_CODEX_SANDBOX_MODE=danger-full-access
```

You can pass that override on the source node via `env`, or in the tuner profile `env:` block so the optimizer and generated tuned agent inherit it.

## Smoke

Run the bundled local smoke check:

```bash
agentflow smoke
```

Run the same flow through `run`:

```bash
agentflow run examples/local-real-agents-kimi-smoke.yaml --output summary
```

Use the shell-init or shell-wrapper smoke templates when you want the bootstrap wiring spelled out explicitly.
