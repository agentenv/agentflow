# Pipeline Reference

Pipeline authoring details, execution targets, and per-agent launch behavior.

## Python DAG

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

Use `fanout(node, source)` to fan a node into parallel copies and `merge(node, source, by=...|size=...)` to reduce them.
`DAG(...)` also accepts `fail_fast`, `node_defaults`, `agent_defaults`, and `local_target_defaults`.
Use `dag.to_json()` to serialize a compact runnable pipeline, `dag.to_payload()` for the raw object structure, and `dag.to_spec()` for the fully expanded in-memory pipeline object.

See `examples/airflow_like.py` for the small static DAG. `examples/airflow_like_fuzz_batched.py` and `examples/airflow_like_fuzz_grouped.py` are advanced fanout examples.

## Pipeline schema

Each node supports:

- `agent`: `codex`, `claude`, or `kimi`
- `fanout`: `count`, `values`, `matrix`, `group_by`, or `batches`, plus optional `as`, `derive`, and matrix-only `include` / `exclude`
- `schedule`: optional periodic execution for local nodes with `every_seconds`, `until_fanout_settles_from`, and optional `actuation`
- `model`: any model string understood by the backend
- `provider`: a string or a structured provider config with `base_url`, `api_key_env`, headers, and env
- `tools`: `read_only` or `read_write`
- `repo_instructions_mode`: `inherit` (default) or `ignore` for agent CLIs that should not absorb repo-local instruction files such as `AGENTS.md`, `CLAUDE.md`, or project skills
- `mcps`: a list of MCP server definitions
- `skills`: a list of local skill paths or names
- `target_skill_policy`: `none` (default) or `inherit_all` for package-style skill refs such as `static-analysis::semgrep`
- `target`: `local`, `container`, `ssh`, `ec2`, or `ecs`
- local target fields: `cwd`, `bootstrap`, `shell`, `shell_login`, `shell_interactive`, and `shell_init`
- `capture`: `final` or `trace`
- `retries` and `retry_backoff_seconds`
- `success_criteria`: output or filesystem checks evaluated after execution

Skill entries are resolved from the pipeline `working_dir`. You can point `skills:` at a plain file, a `.md` file, a home-relative path such as `~/.codex/skills/release-skill`, or a directory that contains `SKILL.md`.

## Skill policy boundary

AgentFlow keeps repo-local instruction files and repo-local skill packages separate:

- `repo_instructions_mode` controls whether the underlying agent CLI can absorb repo-local instruction files such as `AGENTS.md`, `CLAUDE.md`, or similar instruction documents.
- Plain `skills:` paths still resolve from the pipeline `working_dir`. If you point at `skills/release-skill` or `./docs/checklist.md`, that is an explicit local file choice, not package discovery.
- Package-style skill refs such as `static-analysis::semgrep` resolve from AgentFlow-owned `.agents/skills/` roots by default.
- The target repo's `.agents/skills/` packages are ignored by default. Opt in per node with `target_skill_policy: inherit_all` when you explicitly trust that repo-local package source.
- Even with `target_skill_policy: inherit_all`, AgentFlow-owned skill roots stay authoritative and are searched first. Explicit trust only adds the target repo's `.agents/skills/` root after the owned roots.

Example:

```json
{
  "nodes": [
    {
      "id": "plan",
      "agent": "codex",
      "repo_instructions_mode": "ignore",
      "skills": ["static-analysis::semgrep"]
    },
    {
      "id": "trusted-review",
      "agent": "claude",
      "repo_instructions_mode": "ignore",
      "target_skill_policy": "inherit_all",
      "skills": ["repo-only-review::default"]
    }
  ]
}
```

In that example, `repo_instructions_mode: ignore` still only affects instruction-file discovery. The second node separately opts into trusting the target repo's `.agents/skills/` packages.

Top-level pipeline controls include:

- `concurrency`: max parallel nodes within a run
- `fail_fast`: skip downstream work after the first failed node
- `node_defaults`: shared node fields merged into every node before validation
- `agent_defaults`: agent-specific shared node fields keyed by `codex`, `claude`, or `kimi`

`node_defaults` is the pipeline-wide baseline. `agent_defaults` is the agent-specific override layer. Explicit node values always win.

```python
DAG(
    "demo",
    node_defaults={
        "agent": "codex",
        "tools": "read_only",
        "capture": "final",
    },
    agent_defaults={
        "codex": {
            "model": "gpt-5-codex",
            "retries": 1,
            "retry_backoff_seconds": 1,
            "extra_args": ["--search", "-c", 'model_reasoning_effort="high"'],
        }
    },
)
```

## Fan-out and merge

Use `fanout()` when a DAG needs many nearly identical nodes. Use `merge()` to reduce them. AgentFlow expands those nodes into a concrete DAG before validation and execution.

```python
from agentflow import DAG, codex, fanout, merge

with DAG("sweep-demo", concurrency=8) as dag:
    review = fanout(
        codex(task_id="review", prompt="Shard {{ item.number }} of {{ item.count }}."),
        8,
    )
    final = codex(
        task_id="merge",
        prompt="{% for s in fanouts.review.nodes %}{{ s.output }}\n{% endfor %}",
    )
    review >> final
```

### `item` shape (fanout)

Every expanded copy gets an `item` template variable:

| Field | Type | Example |
| --- | --- | --- |
| `item.index` | int | 0, 1, 2, ... |
| `item.number` | int | 1, 2, 3, ... (1-indexed) |
| `item.count` | int | total copies |
| `item.suffix` | str | "0", "01", "001" (zero-padded) |
| `item.node_id` | str | "review_001" |
| `item.value` | Any | the raw iteration value |
| `item.<key>` | Any | dict keys from value are lifted (e.g. `item.target`) |
| `item.<key>` | Any | keys from `derive={}` are added |

### `item` shape (merge)

Reducer nodes get everything above plus:

| Field | Type | Description |
| --- | --- | --- |
| `item.source_group` | str | task_id of the fanout being reduced |
| `item.source_count` | int | total members in the source fanout |
| `item.member_ids` | list | node IDs of members in this group/batch |
| `item.members` | list | full member objects |
| `item.size` | int | members in this group/batch |

With `size=` (batches): `item.start_number`, `item.end_number`, `item.start_index`, `item.end_index`.

With `by=` (groups): the grouping field values are on `item` directly (e.g. `item.target`).

At runtime, `item.scope` provides aggregated results:

| Field | Type | Description |
| --- | --- | --- |
| `item.scope.ids` | list | member node IDs |
| `item.scope.size` | int | count |
| `item.scope.nodes` | list | member objects with status/output |
| `item.scope.outputs` | list | output strings |
| `item.scope.summary` | dict | {total, completed, failed, with_output, ...} |
| `item.scope.with_output` | subset | members with non-empty output |
| `item.scope.without_output` | subset | members with empty output |

### Source types

`fanout(node, source)` dispatches on type:

- `int` -- count: `fanout(node, 128)`
- `list` -- values: `fanout(node, [{"repo": "api"}, {"repo": "billing"}])`
- `dict` -- matrix (cartesian product): `fanout(node, {"repo": [...], "check": [...]})`

Matrix supports `include=` and `exclude=` for curated adjustments.

### Reducer modes

`merge(node, source_node)` requires exactly one of `by=` or `size=`:

- `by=["field", ...]` -- one reducer per unique field combination
- `size=N` -- one reducer per N-item batch

### Derived fields

Add computed fields with `derive=`:

```python
fanout(
    codex(task_id="review", prompt="Work in {{ item.workspace }}"),
    {"repo": [{"name": "api"}, {"name": "billing"}], "check": [{"kind": "security"}]},
    derive={
        "label": "{{ item.name }}/{{ item.kind }}",
        "workspace": "agents/{{ item.name }}_{{ item.kind }}_{{ item.suffix }}",
    },
)
```

### Expansion rules

- A fan-out node expands to `review_0` through `review_7` (zero-padded when needed).
- Dict keys from values are lifted onto `item` (e.g. `item.target`).
- Matrix expands the cartesian product in declaration order.
- `merge` with `by=` creates one reducer per unique field combination.
- `merge` with `size=` partitions into fixed-size batches.
- A downstream `>>` dependency on a fanout node expands to all its members.
- `derive` fields render in declaration order after base expansion.

## Periodic nodes

Use `schedule` when one node should re-run on a fixed interval inside the same pipeline execution.

```python
monitor = codex(
    task_id="monitor",
    schedule={
        "every_seconds": 600,
        "until_fanout_settles_from": "worker",
        "actuation": "output_json",
    },
    prompt=(
        "Tick {{ item.tick_number }}\n"
        "{% for shard in fanouts.worker.nodes %}\n"
        "- {{ shard.id }} stdout={{ shard.artifacts.stdout_log }}\n"
        "{% endfor %}"
    ),
)
```

Periodic nodes are local-only in v1. They stop automatically once the watched fanout group reaches terminal state.

With `actuation: output_json`, the node may emit a JSON envelope with an `analysis` string plus `cancel` / `rerun` actions for members of the watched fanout group.

Runtime numeric settings are validated up front: `concurrency` must be at least `1`, `timeout_seconds` must be greater than `0`, and both `retries` and `retry_backoff_seconds` must be non-negative.

MCP definitions are also validated before launch: `stdio` servers require `command` and reject HTTP-only fields such as `url`, `streamable_http` servers require `url` and reject stdio-only fields such as `command`, and MCP server names must be unique within a node.

`repo_instructions_mode: ignore` is a generic AgentFlow switch with agent-specific implementations. The current adapters use the same high-level pattern: start the agent from an isolated runtime directory, keep the target repo accessible via an explicit allowlist flag such as `--add-dir`, and disable or override repo-local instruction discovery where the underlying CLI supports it. When you enable this mode, write prompts that use absolute paths or explicitly tell the agent to `cd` into the repository before running shell commands. This switch does not change package-style skill discovery under `.agents/skills/`; use `target_skill_policy` for that boundary.

Built-in provider shorthands:

- `codex`: `openai`
- `claude`: `anthropic`, `kimi`
- `kimi`: `kimi`, `moonshot`, `moonshot-ai`

`provider: kimi` is intentionally rejected on `codex` nodes. Codex requires an OpenAI Responses API backend, and Kimi's public endpoints do not expose `/responses`.

When both `provider.env` and `node.env` define the same variable, `node.env` wins. For Claude-compatible Kimi setups, `doctor` and `inspect` also recognize providers that set `ANTHROPIC_BASE_URL=https://api.kimi.com/coding/` in `provider.env` even when `provider.base_url` is omitted.

## Execution targets

### Local

Runs the prepared agent command directly on the host. Set `target.shell` to wrap the command in a specific shell, such as `bash -lc`. If you provide a shell name without an explicit command flag, AgentFlow uses `-c` by default. Opt into startup file loading with `shell_login: true` and `shell_interactive: true`.

`target.cwd` controls the local node working directory. Absolute paths are used as-is; relative paths are resolved from the pipeline `working_dir`. AgentFlow creates that directory right before launch when it does not already exist.

The local bootstrap fields `shell_login`, `shell_interactive`, and `shell_init` require `target.shell`. For the common Kimi helper case, `target.bootstrap: kimi` expands to the same `bash` + login + interactive + `shell_init` setup automatically.

```python
target={"bootstrap": "kimi"}
```

When most local nodes share the same shell bootstrap, move that block to top-level `local_target_defaults` and only override the nodes that differ.

```python
DAG(
    "demo",
    local_target_defaults={"bootstrap": "kimi"},
)
```

If one local node should not inherit the shared bootstrap, set `target={"bootstrap": None}` on that node.
`shell_init` is treated as a bootstrap prerequisite: if it exits non-zero, AgentFlow does not launch the wrapped agent command.

### Container

Wraps the command in `docker run`, mounts the working directory, runtime directory, and the AgentFlow app, then streams stdout and stderr back into the run trace.

## Agent notes

### Codex

- Uses `codex exec --json`
- Maps tools mode to Codex sandboxing
- Keeps model-only Codex nodes on the ambient CLI login path instead of forcing an isolated `CODEX_HOME`
- Writes `CODEX_HOME/config.toml` only when provider or MCP selection requires an isolated home

### Claude

- Uses `claude -p ... --output-format stream-json --verbose`
- Passes `--tools` according to the read-only vs read-write policy
- Writes a per-node MCP JSON config and passes it with `--mcp-config`

### Kimi

- Uses the active Python interpreter via `sys.executable -m agentflow.remote.kimi_bridge`
- Emits a Kimi-style JSON-RPC event stream
- Calls Moonshot's OpenAI-compatible chat completions API
- Provides a small built-in tool layer for read, search, write, and shell actions
