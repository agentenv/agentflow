# AgentFlow

Orchestrate codex, claude, and kimi agents in dependency graphs with parallel fanout, iterative cycles, and remote execution on SSH/EC2/ECS.

![AgentFlow Graph](docs/graph.png)
*94-node pipeline: plan → 64 workers → 8 batch merges → 16 reviews → 4 review merges → synthesis*

## Install / Upgrade

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

## Local & External Models via Pi

Use the `pi` coding agent as a target alongside `codex` and `claude`. Pi routes
to Anthropic, OpenAI, Groq, Cerebras, xAI, DeepSeek, Gemini, OpenRouter, Bedrock,
etc., and to local endpoints (LMStudio, Ollama) via its OpenAI-compatible or
Anthropic-compatible wire protocols.

```python
from agentflow import Graph, codex, pi

with Graph("mixed") as g:
    # External: Claude via Pi
    review = pi(
        task_id="review",
        prompt="Review {{ nodes.impl.output }}",
        model="anthropic/claude-sonnet-4-6:high",
    )

    # Local: LMStudio (add the provider once in ~/.pi/agent/models.json)
    scan = pi(
        task_id="scan",
        prompt="Scan the repo for TODOs.",
        model="lmstudio/qwen/qwen3.6-27b",
        tools="read_only",
    )
```

For one-off inline provider configs (e.g. a remote LMStudio box), pass a full
`ProviderConfig` via `provider={...}` and AgentFlow materializes a scoped
`models.json` for the run. See `examples/pi_local_lmstudio.py`.

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

## Tuned Agent Evolution

Use a completed Codex run as training data to create a reusable tuned agent:

```python
from agentflow import Graph, codex, evolve

with Graph("improve-codex", working_dir=".") as g:
    source = codex(task_id="plan", prompt="Inspect this repo and summarize the main risks.")
    tuned = evolve(source, target="codex", optimizer="codex")

print(g.to_json())
```

Run order:

```bash
agentflow run pipeline.py
agentflow evolve <run_id> -n <node_id> --target codex --profile codex --optimizer codex
agentflow tuned-agents
agentflow tuned-agent codex_tuned --output json
```

Successful evolutions are stored under `.agentflow/tuned_agents/<name>/versions/<version>/` with copied traces, the cloned repo, and version metadata. Tuned agents currently resolve only on local targets.

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

## Graph Optimization Rounds

Run multiple optimization rounds over your graph with top-level `optimizer` and `n_run`. Use this when you want AgentFlow to let the optimizer rewrite the graph between rounds; the validation step only checks that the edited pipeline loads and passes schema validation, not that the edits are semantically better.

Artifacts and logs for each round live under `.agentflow/runs/<run_id>/optimization/round-XXX/`.

```python
from agentflow import Graph, codex

with Graph(
    "optimization-demo",
    optimizer="codex",
    n_run=2,
    concurrency=2,
) as g:
    plan = codex(task_id="plan", prompt="Outline the tasks required to finish the ticket.")
    review = codex(task_id="review", prompt="Review the plan for missing steps or risks.")
    summary = codex(task_id="summary", prompt="Summarize the approved plan and next actions.")
    plan >> review >> summary

print(g.to_json())
```

## CLI

```bash
agentflow run pipeline.py           # run a pipeline
agentflow run pipeline.py --output summary
agentflow evolve <run_id> -n plan   # evolve a tuned agent from prior Codex traces
agentflow tuned-agents              # list locally registered tuned agents
agentflow tuned-agent codex_tuned   # inspect one tuned agent
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
