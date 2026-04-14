---
name: agentflow
description: Run AI agents and build multi-agent pipelines using AgentFlow. Use when the user wants to call codex, claude, kimi, or gemini agents — either as a single one-off call or orchestrated in parallel, in sequence, or in iterative loops. Trigger when the user mentions running an agent, multi-agent workflows, fan-out tasks, code review pipelines, iterative implementation loops, running agents on EC2/ECS, or any task that needs AI agents coordinated together. Also trigger for "agentflow", "pipeline", "graph of agents", "fanout", "shard", "run codex on remote", "exec agent", or "call gemini/claude/codex". For details on exec and inline execution, read references/exec.md.
---

# AgentFlow

Run AI agents directly or build multi-agent pipelines where codex, claude, kimi, and gemini work together in dependency graphs with parallel fanout, iterative cycles, and remote execution.

## Quick Start: Single Agent Call

The fastest way to run an agent — no files needed:

```bash
agentflow exec gemini "What's trending on GitHub?" --model gemini-3-pro-preview
agentflow exec claude "Explain this codebase" --tools read_only
agentflow exec codex "Fix the failing test" --tools read_write
agentflow exec shell "ls -la"
```

`exec` prints the agent's response directly. Use `--output text` to force raw text, `--output json` for structured output. See `references/exec.md` for all options.

## Quick Start: Pipeline

For multi-step workflows, define a pipeline:

```python
from agentflow import Graph, codex, claude

with Graph("review-pipeline", concurrency=3) as g:
    plan = codex(task_id="plan", prompt="Plan the work.", tools="read_only")
    impl = claude(task_id="impl", prompt="Implement:\n{{ nodes.plan.output }}", tools="read_write")
    review = codex(task_id="review", prompt="Review:\n{{ nodes.impl.output }}")
    plan >> impl >> review

print(g.to_json())
```

Run: `agentflow run pipeline.py`

Or inline without a file:

```bash
# Inline JSON
agentflow run -e '{"name":"review","nodes":[{"id":"a","agent":"codex","prompt":"Plan the work"},{"id":"b","agent":"claude","prompt":"Implement: {{ nodes.a.output }}","depends_on":["a"]}]}'

# From stdin
python3 pipeline.py | agentflow run -
```

## Imports

```python
from agentflow import Graph, codex, claude, kimi, gemini  # agents
from agentflow import fanout, merge                    # parallel shards
from agentflow import shell, python_node, sync         # utility nodes
```

## Nodes

Create agent nodes with `codex()`, `claude()`, `kimi()`, or `gemini()`. Required: `task_id`, `prompt`.

```python
codex(
    task_id="name",              # unique ID (required)
    prompt="...",                 # Jinja2 template (required)
    tools="read_only",           # "read_only" | "read_write"
    timeout_seconds=300,
    retries=1,
    success_criteria=[{"kind": "output_contains", "value": "PASS"}],
    target={...},                # execution target (local/ssh/ec2/ecs)
    env={"KEY": "val"},
)
```

## Dependencies

Use `>>` to set execution order:

```python
plan >> [impl, review]       # plan before impl AND review (parallel)
[impl, review] >> merge      # both before merge
```

## Template Variables

Prompts are Jinja2 templates rendered at runtime:

```
{{ nodes.plan.output }}              # output of completed node
{{ nodes.plan.status }}              # "completed", "failed"
{{ fanouts.shards.nodes }}           # all fanout members
{{ fanouts.shards.summary.completed }}
{{ item.number }}                    # current fanout member fields
```

## Fanout (Parallel Shards)

`fanout(node, source)` -- source type determines mode:

```python
# int = count (N identical copies)
shards = fanout(codex(task_id="shard", prompt="Shard {{ item.number }}/{{ item.count }}"), 128)

# list = values (one per item)
reviews = fanout(
    codex(task_id="review", prompt="Review {{ item.repo }}"),
    [{"repo": "api"}, {"repo": "billing"}],
)

# dict = matrix (cartesian product)
fuzz = fanout(
    codex(task_id="fuzz", prompt="{{ item.target }} + {{ item.sanitizer }}"),
    {"lib": [{"target": "libpng"}], "check": [{"sanitizer": "asan"}, {"sanitizer": "ubsan"}]},
)
```

### item fields

| Field | Type | Example |
|---|---|---|
| `item.index` | int | 0, 1, 2 |
| `item.number` | int | 1, 2, 3 (1-indexed) |
| `item.count` | int | total copies |
| `item.suffix` | str | "000", "001" (zero-padded) |
| `item.node_id` | str | "shard_001" |
| `item.<key>` | Any | dict keys lifted from values |

### derive (computed fields)

```python
fanout(node, 128, derive={"workspace": "agents/{{ item.suffix }}"})
```

## Merge (Reduce Fanout)

`merge(node, source, by=[...] | size=N)`:

```python
# Batch reduce: one reducer per 16 shards
batch = merge(
    codex(task_id="batch", prompt="Reduce shards {{ item.start_number }}-{{ item.end_number }}"),
    shards, size=16,
)

# Group by field value
family = merge(
    codex(task_id="family", prompt="Reduce {{ item.target }}"),
    fuzz, by=["target"],
)
```

Merge adds: `item.member_ids`, `item.members`, `item.size`, `item.source_group`.
At runtime: `item.scope.nodes`, `item.scope.outputs`, `item.scope.summary`, `item.scope.with_output`.

## Cycles (Iterative Loops)

Use `on_failure` back-edges for retry-until-success patterns:

```python
with Graph("iterative", max_iterations=5) as g:
    write = codex(task_id="write", prompt=(
        "Write the code.\n"
        "{% if nodes.review.output %}Fix: {{ nodes.review.output }}{% endif %}"
    ), tools="read_write")
    review = claude(task_id="review", prompt=(
        "Review:\n{{ nodes.write.output }}\n"
        "If complete, say LGTM. Otherwise list issues."
    ), success_criteria=[{"kind": "output_contains", "value": "LGTM"}])
    done = codex(task_id="done", prompt="Summarize:\n{{ nodes.write.output }}")

    write >> review
    review.on_failure >> write   # loop back until LGTM
    review >> done               # exit on success
```

## Execution Targets

### Local (default)
No `target` needed. Runs on the host machine.

### SSH
```python
target={"kind": "ssh", "host": "server", "username": "deploy"}
# forward_credentials=True to override remote with local codex/claude/kimi auth
target={"kind": "ssh", "host": "server", "forward_credentials": True}
```

### EC2 (auto-discovers AMI, key pair, VPC)
```python
target={"kind": "ec2", "region": "us-east-1"}
# Optional: instance_type, terminate, snapshot, shared, spot
```

### ECS Fargate (auto-discovers VPC, builds agent image)
```python
target={"kind": "ecs", "region": "us-east-1"}
# Optional: image, cpu, memory, install_agents, cluster
```

### Shared instances
Same `shared` ID = same instance across nodes:

```python
plan = codex(task_id="plan", ..., target={"kind": "ec2", "shared": "dev"})
impl = codex(task_id="impl", ..., target={"kind": "ec2", "shared": "dev"})
# Both run on same EC2, files persist between them
```

## Worktrees

Isolate each agent in its own git worktree so they can edit files without conflicts:

```python
with Graph("review", use_worktree=True) as g:
    reviewers = fanout(
        codex(task_id="reviewer", prompt="Review {{ item.file }}", tools="read_write"),
        [{"file": "api.py"}, {"file": "auth.py"}, {"file": "db.py"}],
    )
```

Each agent gets a full repo copy at `.agentflow/worktrees/<run_id>/<node_id>/`. Cleaned up after execution.

## Utility Nodes

Non-LLM nodes for deterministic operations (no API calls, instant execution):

```python
# Run a shell script
build = shell(task_id="build", script="npm run build && echo OK")

# Run Python code
validate = python_node(task_id="validate", code="import json; print(json.dumps({'ok': True}))")

# Sync local repo to remote (rclone or tar+ssh fallback)
deploy = sync(task_id="deploy", mode="full", target={
    "kind": "ssh", "host": "server", "username": "deploy", "remote_workdir": "/app",
})
# mode="repo": .git + stash only (lightweight)
# mode="full": entire directory
```

Mix with agent nodes freely: `build >> codex(...) >> deploy`

## Scratchboard

Enable shared memory across all agents:

```python
with Graph("campaign", scratchboard=True) as g:
    ...
```

All agents get a `scratchboard.md` file to read context and write findings.

## Graph Options

```python
Graph("name",
    concurrency=4,          # max parallel nodes
    fail_fast=False,         # skip downstream on failure
    max_iterations=10,       # cycle iteration limit
    scratchboard=False,      # shared memory file
    use_worktree=False,      # git worktree per agent
    node_defaults={...},     # defaults for all nodes
    agent_defaults={...},    # per-agent defaults
)
```

## CLI

```bash
# Single agent call (no file needed)
agentflow exec <agent> "<prompt>" [--model X] [--tools read_only|read_write] [--output text|json]

# Run pipeline from file
agentflow run pipeline.py
agentflow run pipeline.json --output summary

# Run pipeline inline (no file needed)
agentflow run -e '{"name":"q","nodes":[...]}'            # inline JSON
agentflow run -e 'from agentflow import ...; print(...)'  # inline Python
echo '{"name":"q","nodes":[...]}' | agentflow run -       # from stdin

# Inspect and validate
agentflow inspect pipeline.py            # show graph structure
agentflow validate pipeline.py           # check without running
agentflow templates                       # list starter templates
agentflow init > pipeline.py             # scaffold starter
```

For the full exec reference (all flags, output formats, env vars, examples), read `references/exec.md`.
