---
name: agentflow
description: Build and run multi-agent pipelines using AgentFlow. Use when the user wants to orchestrate codex, claude, kimi, or gemini agents in parallel, in sequence, or in iterative loops. Trigger when the user mentions multi-agent workflows, fan-out tasks, code review pipelines, iterative implementation loops, running agents on EC2/ECS, or any task that needs multiple AI agents coordinated together. Also trigger for "agentflow", "pipeline", "graph of agents", "fanout", "shard", or "run codex on remote".
---

# AgentFlow

Build multi-agent pipelines where codex, claude, kimi, and gemini work together in dependency graphs with parallel fanout, iterative cycles, and remote execution.

## Quick Start

```python
from agentflow import Graph, codex, claude

with Graph(
    "review-pipeline",
    concurrency=3,
    optimizer="codex",
    n_run=2,
) as g:
    plan = codex(task_id="plan", prompt="Plan the work.", tools="read_only")
    impl = claude(task_id="impl", prompt="Implement:\n{{ nodes.plan.output }}", tools="read_write")
    review = codex(task_id="review", prompt="Review:\n{{ nodes.impl.output }}")
    plan >> impl >> review

print(g.to_json())
```

Run: `agentflow run pipeline.py`

Setting `optimizer` and `n_run` like this runs graph optimization between rounds, so Codex can rewrite the graph and the runtime can verify that the edited pipeline still loads and matches schema before the next round runs.

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

Add optimizer controls when you want AgentFlow to rewrite the graph before execution:

- `optimizer`: the interactive agent (one of `codex`, `claude`, `kimi`) that rewrites the graph for the next round.
- `n_run`: total number of graph rounds to execute; set it to `2` or higher to enable optimization rounds before the final run.

## Graph Optimization Rounds

Use top-level `optimizer` and `n_run` to run optimization rounds on the pipeline graph before execution.
Supported optimizers: `codex`, `claude`, `kimi`.
Set `n_run > 1` to enable per-round optimization behavior.

Artifacts and logs are written under `.agentflow/runs/<run_id>/optimization/round-XXX/`:
- `pipeline.original.py`
- `pipeline.edited.py`
- `graph_report.json`
- `optimizer-prompt.txt`
- `optimizer-result.json`
- `optimizer-validation.json`

Example pipeline with optimizer rounds:

```python
from agentflow import Graph, codex

with Graph(
    "optimization-demo",
    optimizer="codex",
    n_run=2,
    concurrency=2,
) as g:
    plan = codex(task_id="plan", prompt="Outline the tasks in the ticket.")
    review = codex(task_id="review", prompt="Review the plan for missing steps.")
    summary = codex(task_id="summary", prompt="Summarize approved next actions.")
    plan >> review >> summary

print(g.to_json())
```

## CLI

```bash
agentflow run pipeline.py                # run pipeline
agentflow run pipeline.py --output summary
agentflow inspect pipeline.py            # show graph structure
agentflow validate pipeline.py           # check without running
agentflow templates                       # list starter templates
agentflow init > pipeline.py             # scaffold starter
```
