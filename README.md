# AgentFlow

Orchestrate codex, claude, and kimi agents in dependency graphs with parallel fanout, iterative cycles, and remote execution on SSH/EC2/ECS.

![AgentFlow Graph](docs/graph.png)
*94-node pipeline: plan → 64 workers → 8 batch merges → 16 reviews → 4 review merges → synthesis*

## Install

One line:

```bash
curl -fsSL https://raw.githubusercontent.com/shouc/agentflow/master/install.sh | bash
```

This installs agentflow, adds it to PATH, and installs the skill for Codex and Claude Code.

Or manually:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
```

To build the dashboard (optional, requires Node.js):
```bash
make install-ui
make build-ui
```

## Quick Start

```python
from agentflow import Graph, codex, claude

with Graph("my-pipeline", concurrency=3) as g:
    plan = codex(task_id="plan", prompt="Inspect the repo and plan the work.", tools="read_only")
    impl = claude(task_id="impl", prompt="Implement the plan:\n{{ nodes.plan.output }}", tools="read_write")
    review = codex(task_id="review", prompt="Review:\n{{ nodes.impl.output }}")
    plan >> impl >> review

print(g.to_json())
```

```bash
agentflow run pipeline.py --output summary
```

Or just ask Codex (the agentflow skill is auto-installed):

```bash
codex "Use agentflow to fan out 10 codex agents, each telling a unique joke, then merge their outputs and pick the funniest one. Write the pipeline and run it."
```

## Parallel Fanout

Fan a node into many parallel copies with `fanout()`:

```python
from agentflow import Graph, codex, fanout, merge

with Graph("code-review", concurrency=8) as g:
    scan = codex(task_id="scan", prompt="List the top 5 files to review.")
    review = fanout(
        codex(task_id="review", prompt="Review {{ item.file }}:\n{{ nodes.scan.output }}"),
        [{"file": "api.py"}, {"file": "auth.py"}, {"file": "db.py"}],
    )
    summary = codex(task_id="summary", prompt=(
        "Merge findings:\n{% for r in fanouts.review.nodes %}{{ r.output }}\n{% endfor %}"
    ))
    scan >> review >> summary

print(g.to_json())
```

`fanout(node, source)` dispatches on type:
- `int` -- N identical copies: `fanout(node, 128)`
- `list` -- one per item: `fanout(node, [{"repo": "api"}, ...])`
- `dict` -- cartesian product: `fanout(node, {"axis1": [...], "axis2": [...]})`

Reduce with `merge(node, source, size=N)` (batch) or `merge(node, source, by=["field"])` (group).

## Iterative Cycles

Loop until a stop condition with `on_failure`:

```python
from agentflow import Graph, codex, claude

with Graph("iterative-impl", max_iterations=5) as g:
    write = codex(
        task_id="write",
        prompt="Write a Python email validator.\n{% if nodes.review.output %}Fix: {{ nodes.review.output }}{% endif %}",
        tools="read_write",
    )
    review = claude(
        task_id="review",
        prompt="Review:\n{{ nodes.write.output }}\nIf complete, say LGTM. Otherwise list issues.",
        success_criteria=[{"kind": "output_contains", "value": "LGTM"}],
    )
    write >> review
    review.on_failure >> write  # loop until LGTM or max_iterations

print(g.to_json())
```

## Remote Execution

Run agents on remote machines -- zero config needed:

```python
# EC2 (auto-discovers AMI, key pair, VPC)
codex(task_id="remote", prompt="...", target={"kind": "ec2", "region": "us-east-1"})

# ECS Fargate (auto-discovers VPC, builds agent image)
codex(task_id="remote", prompt="...", target={"kind": "ecs", "region": "us-east-1"})

# SSH
codex(task_id="remote", prompt="...", target={"kind": "ssh", "host": "server", "username": "deploy"})
```

Shared instances across nodes:

```python
plan = codex(task_id="plan", prompt="...", target={"kind": "ec2", "shared": "dev-box"})
impl = codex(task_id="impl", prompt="...", target={"kind": "ec2", "shared": "dev-box"})
plan >> impl  # same EC2 instance, files persist
```

## Scratchboard

Shared memory file across all agents:

```python
with Graph("campaign", scratchboard=True) as g:
    shards = fanout(codex(task_id="fuzz", prompt="..."), 128)
```

## Examples

| Example | What it does |
|---|---|
| `airflow_like.py` | Basic pipeline: plan → implement → review → merge |
| `code_review.py` | Fan out code review across files, merge findings |
| `dep_audit.py` | Audit each dependency for security/license issues |
| `test_gap.py` | Find untested modules, suggest tests per module |
| `multi_agent_debate.py` | Codex vs Claude: independent solve + cross-critique |
| `release_check.py` | Parallel release gate: tests + security + changelog |
| `iterative_impl.py` | Write → review → fix cycle until LGTM |
| `airflow_like_fuzz_batched.py` | 128-shard fanout with batch merge + periodic monitor |
| `airflow_like_fuzz_grouped.py` | Matrix fanout with grouped reducers |
| `ec2_remote.py` | Run codex on a remote EC2 instance |
| `ecs_fargate.py` | Run codex on ECS Fargate |

## CLI

```bash
agentflow run pipeline.py           # run a pipeline
agentflow run pipeline.py --output summary
agentflow inspect pipeline.py       # show expanded graph
agentflow validate pipeline.py      # check without running
agentflow templates                  # list starter templates
agentflow init > pipeline.py        # scaffold a starter
```


## Acknowledgements

* [gepa](https://github.com/gepa-ai/gepa)
* [kiss-ai](https://github.com/ksenxx/kiss_ai)
* [claude-code-telegram](https://github.com/RichardAtCT/claude-code-telegram)
* [linux.do](https://linux.do)
