# Pipeline Reference

Pipeline authoring details, execution targets, and per-agent launch behavior.

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
- `fanout`: expand one node definition into concrete nodes before validation; accepts `count`, `values`, `values_path`, `matrix`, `matrix_path`, or `group_by`, plus optional `as`, `derive`, and matrix-only `include` / `exclude`
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

## Fan-out nodes

Use `fanout` when a DAG needs many nearly identical nodes, such as repository sweeps, fuzzing swarms, or shardable audits.
AgentFlow expands those nodes into an ordinary concrete DAG before validation and execution, so the orchestrator, runners, and persisted runs still operate on normal node ids.

```yaml
nodes:
  - id: fuzz
    fanout:
      count: 8
      as: shard
    agent: codex
    prompt: |
      You are shard {{ shard.number }} of {{ shard.count }}.
      Use suffix {{ shard.suffix }} for any per-shard paths or seeds.

  - id: merge
    agent: codex
    depends_on: [fuzz]
    prompt: |
      {% for shard in fanouts.fuzz.nodes %}
      ## {{ shard.id }}
      {{ shard.output or "(no output)" }}

      {% endfor %}
```

For a practical swarm authoring workflow, scaffold a ready-made Codex fuzz swarm and then resize it instead of hand-editing the YAML from scratch:

```bash
agentflow init fuzz-swarm.yaml --template codex-fuzz-swarm
agentflow init fuzz-128.yaml --template codex-fuzz-swarm --set shards=128 --set concurrency=32
agentflow init fuzz-hierarchical-grouped.yaml --template codex-fuzz-hierarchical-grouped
agentflow init fuzz-hierarchical-grouped-128.yaml --template codex-fuzz-hierarchical-grouped --set bucket_count=8 --set concurrency=32
agentflow init fuzz-hierarchical.yaml --template codex-fuzz-hierarchical-manifest
agentflow init fuzz-hierarchical-128.yaml --template codex-fuzz-hierarchical-manifest --set bucket_count=8 --set concurrency=32
agentflow init fuzz-hierarchical-128.yaml --template codex-fuzz-hierarchical-128
agentflow inspect fuzz-128.yaml --output summary
```

The checked-in [`examples/fuzz/fuzz_codex_32.yaml`](/home/shou/agentflow/examples/fuzz/fuzz_codex_32.yaml) file is the default 32-shard starter rendered by that template. [`examples/fuzz/fuzz_codex_128.yaml`](/home/shou/agentflow/examples/fuzz/fuzz_codex_128.yaml) remains the fixed large-fanout reference when you want to inspect a full 128-node spec directly from the repo. When you want the same 128-shard scale with the axis catalog split into a support file, inspect [`examples/fuzz/codex-fuzz-matrix-manifest-128.yaml`](/home/shou/agentflow/examples/fuzz/codex-fuzz-matrix-manifest-128.yaml).
When that same staged-reducer pattern should stay maintainable and the reducer roster can be derived from shard metadata already in the fanout, start from [`examples/fuzz/codex-fuzz-hierarchical-grouped.yaml`](/home/shou/agentflow/examples/fuzz/codex-fuzz-hierarchical-grouped.yaml) or `agentflow init fuzz-hierarchical-grouped.yaml --template codex-fuzz-hierarchical-grouped`, which keep only the shard axes manifest and derive per-family reducers through `fanout.group_by`; raise `--set bucket_count=8 --set concurrency=32` when you want that same sidecar-manifest pattern at 128 shards without hand-editing a second roster file. When reducers need an explicitly maintainer-owned roster that can diverge from the shard axes, start from [`examples/fuzz/codex-fuzz-hierarchical-manifest.yaml`](/home/shou/agentflow/examples/fuzz/codex-fuzz-hierarchical-manifest.yaml) or `agentflow init fuzz-hierarchical.yaml --template codex-fuzz-hierarchical-manifest`, which render both the shard axes manifest and the family reducer roster from one scaffold. [`examples/fuzz/codex-fuzz-hierarchical-128.yaml`](/home/shou/agentflow/examples/fuzz/codex-fuzz-hierarchical-128.yaml) remains the fixed 128-shard staged reducer reference that summarizes each target family before the final maintainer merge by using the `fanouts.<group>.summary`, `completed`, `failed`, and `with_output` prompt helpers. The manifest-backed single-reducer scaffold still lives at [`examples/fuzz/codex-fuzz-matrix-manifest.yaml`](/home/shou/agentflow/examples/fuzz/codex-fuzz-matrix-manifest.yaml) and is the default output of `agentflow init fuzz-matrix-manifest.yaml --template codex-fuzz-matrix-manifest`.

When each shard needs its own structured metadata, use `fanout.values` instead of `count`:

```yaml
nodes:
  - id: fuzz
    fanout:
      as: shard
      values:
        - target: libpng
          sanitizer: asan
          seed: 1101
        - target: sqlite
          sanitizer: ubsan
          seed: 2202
    agent: codex
    prompt: |
      Fuzz {{ shard.target }} with {{ shard.sanitizer }} using seed {{ shard.seed }}.
```

When you need reusable computed shard metadata such as labels, workdirs, or report paths, add `fanout.derive`. AgentFlow renders those fields during fan-out expansion and preserves them everywhere the member metadata is exposed.

```yaml
nodes:
  - id: fuzz
    fanout:
      as: shard
      matrix:
        family:
          - target: libpng
          - target: sqlite
        variant:
          - sanitizer: asan
            seed: 1101
          - sanitizer: ubsan
            seed: 2202
      derive:
        label: "{{ shard.target }}/{{ shard.sanitizer }}/{{ shard.seed }}"
        workspace: "agents/{{ shard.target }}_{{ shard.sanitizer }}_{{ shard.suffix }}"
    agent: codex
    target:
      kind: local
      cwd: "{{ shard.workspace }}"
    prompt: |
      Fuzz {{ shard.label }} inside {{ shard.workspace }}.
```

For large fan-outs, prompt rendering also exposes reducer-friendly fanout subsets and counts. `fanouts.<group>.summary` carries per-status totals plus `with_output` / `without_output`, while `fanouts.<group>.completed`, `failed`, `with_output`, and `without_output` each expose the same `ids`, `size`, `nodes`, `outputs`, `final_responses`, `statuses`, and `values` fields as the full group.

```yaml
nodes:
  - id: family_merge
    fanout:
      as: family
      values:
        - target: libpng
        - target: sqlite
    depends_on: [fuzz]
    prompt: |
      {% set target = "{{ family.target }}" %}
      {% set target_outputs = fanouts.fuzz.with_output.nodes | selectattr("target", "equalto", target) | list %}
      Completed shards: {{ fanouts.fuzz.summary.completed }}
      Failed shards: {{ fanouts.fuzz.summary.failed }}

      {% for shard in target_outputs %}
      ## {{ shard.label }} :: {{ shard.id }}
      {{ shard.output }}

      {% endfor %}
```

When a later Jinja expression needs the fanout member itself, first freeze it into a plain string with a line such as `{% set target = "{{ family.target }}" %}`. Fan-out expansion rewrites the `{{ family.target }}` placeholder before runtime, while the surrounding `{% ... %}` logic still runs later with the normal prompt context.

When the reducer roster should come directly from another fanout's unique member fields, use `fanout.group_by` instead of maintaining a second `values` list:

```yaml
nodes:
  - id: fuzz
    fanout:
      as: shard
      matrix_path: manifests/campaign.axes.yaml
    agent: codex
    prompt: |
      Fuzz {{ shard.target }} with {{ shard.sanitizer }} using seed {{ shard.seed }}.

  - id: family_merge
    fanout:
      as: family
      group_by:
        from: fuzz
        fields: [target, corpus]
    agent: codex
    depends_on: [fuzz]
    prompt: |
      {% set target = "{{ family.target }}" %}
      {% set target_outputs = fanouts.fuzz.with_output.nodes | selectattr("target", "equalto", target) | list %}
      Reduce {{ target }} with {{ target_outputs | length }} ready shard outputs.
```

`fanout.group_by` preserves first-seen order from the source fanout and works with lifted matrix fields such as `target` plus derived fields you computed on the source members.

When a mostly-regular matrix needs a few real-world adjustments, keep the reusable axes and add `fanout.exclude` plus `fanout.include` before moving all the way to a CSV catalog:

```yaml
nodes:
  - id: fuzz
    fanout:
      as: shard
      matrix:
        family:
          - target: libpng
          - target: sqlite
        strategy:
          - sanitizer: asan
            focus: parser
          - sanitizer: ubsan
            focus: stateful
      exclude:
        - target: sqlite
          focus: stateful
      include:
        - family:
            target: openssl
          strategy:
            sanitizer: asan
            focus: handshake
      derive:
        label: "{{ shard.target }}/{{ shard.sanitizer }}/{{ shard.focus }}"
```

When that metadata already lives in a maintainer-owned file, point `fanout.values_path` or `fanout.matrix_path` at it instead of inlining the whole catalog in the pipeline. `values_path` accepts JSON/YAML lists and CSV rows; `matrix_path` accepts JSON/YAML objects with axis lists. Relative paths resolve from the pipeline file, not from `working_dir`.

```yaml
nodes:
  - id: fuzz
    fanout:
      as: shard
      values_path: manifests/shards.csv
    agent: codex
    prompt: |
      Fuzz {{ shard.target }} with seed {{ shard.seed }}.
```

The bundled [`examples/fuzz/codex-fuzz-matrix-manifest.yaml`](/home/shou/agentflow/examples/fuzz/codex-fuzz-matrix-manifest.yaml) example and `agentflow init fuzz-matrix-manifest.yaml --template codex-fuzz-matrix-manifest` scaffold use that `matrix_path` pattern with reusable axes plus derived labels/workdirs. Scale the same template to 128 shards with `agentflow init fuzz-matrix-manifest-128.yaml --template codex-fuzz-matrix-manifest --set bucket_count=8 --set concurrency=32`. When you want the same externalized metadata pattern plus staged reducers derived directly from the shard fanout, the bundled [`examples/fuzz/codex-fuzz-hierarchical-grouped.yaml`](/home/shou/agentflow/examples/fuzz/codex-fuzz-hierarchical-grouped.yaml) example and `agentflow init fuzz-hierarchical-grouped.yaml --template codex-fuzz-hierarchical-grouped` scaffold pair `matrix_path` with `fanout.group_by`, so the reducer targets stay aligned with the same maintainer-owned axes file. When you need staged reducers with an explicit roster that can diverge from those axes, the bundled [`examples/fuzz/codex-fuzz-hierarchical-manifest.yaml`](/home/shou/agentflow/examples/fuzz/codex-fuzz-hierarchical-manifest.yaml) example and `agentflow init fuzz-hierarchical.yaml --template codex-fuzz-hierarchical-manifest` scaffold still pair `matrix_path` with a `values_path` family roster.

CSV-backed `values_path` catalogs are a good fit when each shard genuinely needs explicit per-row metadata that cannot be derived cleanly from reusable axes. The bundled [`examples/fuzz/codex-fuzz-catalog.yaml`](/home/shou/agentflow/examples/fuzz/codex-fuzz-catalog.yaml) example and `agentflow init fuzz-catalog.yaml --template codex-fuzz-catalog` scaffold both use that pattern so a large 128-shard campaign can be retargeted by editing [`examples/fuzz/manifests/codex-fuzz-catalog.csv`](/home/shou/agentflow/examples/fuzz/manifests/codex-fuzz-catalog.csv) instead of touching the reducer or launch settings.

```yaml
nodes:
  - id: fuzz
    fanout:
      as: shard
      matrix_path: manifests/campaign.axes.yaml
    agent: codex
    prompt: |
      Fuzz {{ shard.target }} with {{ shard.sanitizer }} using seed {{ shard.seed }}.
```

When the metadata is naturally multi-axis, use `fanout.matrix` so AgentFlow builds the cartesian product for you:

```yaml
nodes:
  - id: fuzz
    fanout:
      as: shard
      matrix:
        family:
          - target: libpng
            corpus: png
          - target: sqlite
            corpus: sql
        variant:
          - sanitizer: asan
            seed: 1101
          - sanitizer: ubsan
            seed: 2202
    agent: codex
    prompt: |
      Fuzz {{ shard.target }} with {{ shard.sanitizer }} using seed {{ shard.seed }}.

  - id: merge
    agent: codex
    depends_on: [fuzz]
    prompt: |
      {% for shard in fanouts.fuzz.nodes %}
      ## {{ shard.id }} :: {{ shard.target }} / {{ shard.sanitizer }} / {{ shard.seed }}
      {{ shard.output or "(no output)" }}

      {% endfor %}
```

Expansion rules:

- A fan-out node accepts exactly one expansion mode: `count` for homogeneous numeric shards, `values` or `values_path` for explicit per-member metadata, or `matrix` or `matrix_path` for cartesian-product sweeps.
- A fan-out node with `id: fuzz` and `count: 8` expands to `fuzz_0` through `fuzz_7`. The suffix is zero-padded when the fan-out size needs it, so `count: 128` becomes `fuzz_000` through `fuzz_127`.
- When `fanout.values` is used, `count` becomes the list length, `value` holds the raw item, and dictionary item keys with identifier-friendly names are also exposed directly on the alias. That lets `{{ shard.value.seed }}` and `{{ shard.seed }}` both work for `values: [{seed: 1101}]`.
- `fanout.values_path` behaves the same way after AgentFlow loads the referenced list or CSV rows.
- When `fanout.matrix` is used, AgentFlow expands the cartesian product of every axis in declaration order. Each axis item is available under its axis name, and dictionary axis items also lift their identifier-friendly keys onto the alias. That lets `{{ shard.family.target }}` and `{{ shard.target }}` both work when `family` axis items are dictionaries.
- `fanout.matrix_path` behaves the same way after AgentFlow loads the referenced JSON/YAML object.
- `fanout.exclude` removes matrix members whose metadata matches every field in a selector object. Selectors can match lifted fields such as `target` / `focus` or nested axis objects such as `family.target`.
- `fanout.include` appends explicit matrix-style members after exclusions, which is useful for bespoke shards or for adding back one-off combinations without rewriting the whole campaign as a catalog.
- `fanout.derive` can add computed member fields after the base `count`, `values`, `values_path`, `matrix`, or `matrix_path` expansion has been resolved. Derived fields render in declaration order, so later derived fields can reference earlier ones.
- `fanout.as` picks the template variable name for pre-validation substitution. AgentFlow currently expands dotted placeholders rooted at that alias or `fanout`, such as `{{ shard.number }}`, `{{ shard.suffix }}`, `{{ fanout.count }}`, `target.cwd: agents/agent_{{ shard.suffix }}`, or `depends_on: ["prepare_{{ shard.suffix }}"]`.
- Ordinary runtime prompt templates such as `{{ nodes.prepare.output }}` are left intact and still render at execution time.
- A downstream `depends_on: [fuzz]` expands to all members of the `fuzz` group.
- During prompt rendering, `fanouts.<group>.nodes` exposes the grouped member outputs with `id`, `status`, `output`, `final_response`, `stdout`, `stderr`, `trace`, and any preserved fanout member metadata such as `value`, `suffix`, `target`, `seed`, `workspace`, or `label`, plus convenience lists such as `fanouts.<group>.outputs` and `fanouts.<group>.values`.
- Prompt rendering also exposes reducer-friendly subsets: `fanouts.<group>.summary` includes per-status totals plus `with_output` / `without_output`, `fanouts.<group>.status_counts` provides the raw mapping, and `fanouts.<group>.completed`, `failed`, `running`, `pending`, `queued`, `ready`, `retrying`, `cancelled`, `skipped`, `with_output`, and `without_output` each provide the same `ids` / `size` / `nodes` shape as the full group.

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

`target.cwd` controls the local node working directory. Absolute paths are used as-is; relative paths are resolved from the pipeline `working_dir`. AgentFlow creates that local directory right before launch when it does not already exist, which keeps fan-out shard workdirs easy to derive without adding manual bootstrap steps. File-based success criteria such as `file_exists`, `file_contains`, and `file_nonempty` are evaluated from that resolved local node working directory.

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
