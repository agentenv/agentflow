# AgentFlow

AgentFlow orchestrates `codex`, `claude`, and `kimi` as dependency-aware DAGs that can run locally, in containers, or on AWS Lambda.

## Quickstart

Requirements:

- Python 3.11+
- The agent CLIs your pipeline uses (`codex`, `claude`, and/or `kimi`)

Install:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

Scaffold and run a starter pipeline:

```bash
agentflow templates
agentflow init > pipeline.yaml
agentflow init repo-sweep.yaml --template codex-fanout-repo-sweep
agentflow init repo-sweep-batched.yaml --template codex-repo-sweep-batched
agentflow init fuzz-matrix.yaml --template codex-fuzz-matrix
agentflow init fuzz-matrix-derived.yaml --template codex-fuzz-matrix-derived
agentflow init fuzz-matrix-curated.yaml --template codex-fuzz-matrix-curated
agentflow init fuzz-matrix-128.yaml --template codex-fuzz-matrix-128
agentflow init fuzz-hierarchical-grouped.yaml --template codex-fuzz-hierarchical-grouped
agentflow init fuzz-hierarchical-grouped-128.yaml --template codex-fuzz-hierarchical-grouped --set bucket_count=8 --set concurrency=32
agentflow init fuzz-hierarchical.yaml --template codex-fuzz-hierarchical-manifest
agentflow init fuzz-hierarchical-128.yaml --template codex-fuzz-hierarchical-manifest --set bucket_count=8 --set concurrency=32
agentflow init fuzz-hierarchical-128.yaml --template codex-fuzz-hierarchical-128
agentflow init fuzz-matrix-manifest.yaml --template codex-fuzz-matrix-manifest
agentflow init fuzz-matrix-manifest-128.yaml --template codex-fuzz-matrix-manifest --set bucket_count=8 --set concurrency=32
agentflow init fuzz-catalog.yaml --template codex-fuzz-catalog
agentflow init fuzz-catalog-grouped.yaml --template codex-fuzz-catalog-grouped
agentflow init fuzz-batched.yaml --template codex-fuzz-batched
agentflow init fuzz-swarm.yaml --template codex-fuzz-swarm
agentflow init fuzz-128.yaml --template codex-fuzz-swarm --set shards=128 --set concurrency=32
agentflow validate pipeline.yaml
agentflow run pipeline.yaml
```

Useful next commands:

```bash
agentflow inspect pipeline.yaml
agentflow serve --host 127.0.0.1 --port 8000
agentflow smoke
```

Choose a starter:

- `codex-fanout-repo-sweep` for repo review and audit fanout
- `codex-repo-sweep-batched` for 128-shard maintainer sweeps with automatic batch reducers
- `codex-fuzz-matrix` for heterogeneous campaigns built from reusable axes
- `codex-fuzz-matrix-derived` for heterogeneous campaigns that need reusable shard labels and workdirs
- `codex-fuzz-matrix-curated` for heterogeneous campaigns that need a few exclusions or bespoke shards without a catalog
- `codex-fuzz-matrix-128` for a full 128-shard matrix reference
- `codex-fuzz-hierarchical-grouped` for staged reducers derived automatically from the shard fanout
- `codex-fuzz-hierarchical-manifest` for staged reducers whose shard axes and family roster should live in sidecar manifests
- `codex-fuzz-hierarchical-128` for a fixed 128-shard hierarchical reference
- `codex-fuzz-matrix-manifest` for heterogeneous campaigns whose reusable axes should live in a sidecar manifest
- `codex-fuzz-matrix-manifest-128` for a fixed 128-shard manifest-backed reference
- `codex-fuzz-catalog` for spreadsheet-friendly shard catalogs with explicit per-row metadata you cannot derive
- `codex-fuzz-catalog-grouped` for spreadsheet-friendly shard catalogs that still need staged reducers derived automatically from the catalog
- `codex-fuzz-batched` for 128-shard homogeneous swarms that need automatic intermediate reducers
- `codex-fuzz-swarm` for homogeneous shard swarms you resize with `--set shards=...`

## Example

`examples/pipeline.yaml`

```yaml
name: parallel-code-orchestration
description: Codex plans, Claude implements, and Kimi reviews in parallel before a final Codex merge.
working_dir: .
concurrency: 3
nodes:
  - id: plan
    agent: codex
    model: gpt-5-codex
    tools: read_only
    capture: final
    prompt: |
      Inspect the repository and create a short implementation plan.

  - id: implement
    agent: claude
    model: claude-sonnet-4-5
    tools: read_write
    capture: final
    depends_on: [plan]
    prompt: |
      Use the plan below and implement the requested change.

      Plan:
      {{ nodes.plan.output }}

  - id: review
    agent: kimi
    model: kimi-k2-turbo-preview
    tools: read_only
    capture: trace
    depends_on: [plan]
    prompt: |
      Review the proposed implementation plan.

      Plan:
      {{ nodes.plan.output }}

  - id: merge
    agent: codex
    model: gpt-5-codex
    tools: read_only
    depends_on: [implement, review]
    success_criteria:
      - kind: output_contains
        value: success
    prompt: |
      Combine these two perspectives into a final release summary and include the word success.

      Implementation output:
      {{ nodes.implement.output }}

      Review trace:
      {{ nodes.review.output }}
```

For larger swarms, use node-level `fanout` to keep the YAML compact while still running a concrete DAG:

```yaml
nodes:
  - id: fuzzer
    fanout:
      count: 128
      as: shard
    agent: codex
    prompt: |
      You are shard {{ shard.number }} of {{ shard.count }}.

  - id: merge
    agent: codex
    depends_on: [fuzzer]
    prompt: |
      {% for shard in fanouts.fuzzer.nodes %}
      ## {{ shard.id }}
      {{ shard.output or "(no output)" }}

      {% endfor %}
```

When a large pipeline shares the same launch policy across many nodes, lift that into top-level `node_defaults` and `agent_defaults` so only the prompts and dependencies stay local:

```yaml
node_defaults:
  agent: codex
  tools: read_only
  capture: final

agent_defaults:
  codex:
    model: gpt-5-codex
    retries: 1
    retry_backoff_seconds: 1
```

When shards need explicit per-member metadata instead of just an index, use `fanout.values`:

```yaml
nodes:
  - id: fuzzer
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

When the metadata itself is naturally multi-axis, use `fanout.matrix` to build the cartesian product and keep reducer prompts aware of each member's fields:

```yaml
nodes:
  - id: fuzzer
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
    depends_on: [fuzzer]
    prompt: |
      {% for shard in fanouts.fuzzer.nodes %}
      ## {{ shard.id }} :: {{ shard.target }} / {{ shard.sanitizer }} / {{ shard.seed }}
      {{ shard.output or "(no output)" }}

      {% endfor %}
```

When those shards also need reusable computed metadata such as a label or workdir, add `fanout.derive` so you only define that formula once:

```yaml
nodes:
  - id: fuzzer
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
    target:
      kind: local
      cwd: "{{ shard.workspace }}"
```

Local runs create missing `target.cwd` directories automatically right before launch, so fan-out examples only need init steps for genuinely shared directories such as `docs/` or `crashes/`.

When a single 128-shard reducer would be too noisy, prompt rendering also exposes `fanouts.<group>.summary` plus status and output subsets such as `fanouts.<group>.completed`, `fanouts.<group>.failed`, `fanouts.<group>.with_output`, and `fanouts.<group>.without_output`. Each subset keeps the same `ids`, `size`, `nodes`, `outputs`, `final_responses`, `statuses`, and `values` fields, which makes staged reducers easier to write:

```yaml
nodes:
  - id: family_merge
    fanout:
      as: family
      values:
        - target: libpng
        - target: sqlite
    depends_on: [fuzzer]
    prompt: |
      {% set target_outputs = fanouts.fuzzer.with_output.nodes | selectattr("target", "equalto", current.target) | list %}
      Completed shards: {{ fanouts.fuzzer.summary.completed }}
      Failed shards: {{ fanouts.fuzzer.summary.failed }}

      {% for shard in target_outputs %}
      ## {{ shard.label }} :: {{ shard.id }}
      {{ shard.output }}

      {% endfor %}
```

When runtime Jinja needs the current fanout member itself, use `current.*`. It exposes the active node id, agent, dependencies, and lifted fanout metadata, so staged reducers can filter `fanouts.*` or index into `nodes[...]` without the older placeholder-freezing workaround.

When those staged reducers should follow the unique metadata already present on another fanout, use `fanout.group_by` instead of maintaining a second reducer roster:

```yaml
nodes:
  - id: fuzzer
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
        from: fuzzer
        fields: [target, corpus]
    agent: codex
    depends_on: [fuzzer]
    prompt: |
      Reduce {{ current.target }} with {{ current.member_ids | length }} scoped shard inputs.

      {% for shard in current.members %}
      ## {{ shard.node_id }} :: {{ shard.target }} / {{ shard.sanitizer }} / {{ shard.seed }}
      {{ nodes[shard.node_id].output or "(no output)" }}

      {% endfor %}
```

`fanout.group_by` preserves first-seen order from the source fanout and, like `fanout.batches`, now exposes `current.member_ids`, `current.members`, and scoped dependencies that point only at matching source shards. That keeps hierarchical reducers aligned with the underlying shard matrix without a duplicate family manifest and lets them start as soon as their own shard family is ready. The bundled `codex-fuzz-hierarchical-grouped` scaffold uses this pattern and scales to 128 shards with `agentflow init fuzz-hierarchical-grouped-128.yaml --template codex-fuzz-hierarchical-grouped --set bucket_count=8 --set concurrency=32`.

When a homogeneous swarm needs intermediate reducers without maintaining a second roster, use `fanout.batches`. Each batch reducer gets the scoped shard ids in `current.member_ids`, the static shard metadata in `current.members`, and automatically rewritten dependencies that point only at that batch's source shards:

```yaml
nodes:
  - id: fuzzer
    fanout:
      count: 128
      as: shard
      derive:
        workspace: "agents/agent_{{ shard.suffix }}"

  - id: batch_merge
    fanout:
      as: batch
      batches:
        from: fuzzer
        size: 16
    depends_on: [fuzzer]
    prompt: |
      Reduce shards {{ current.start_number }} through {{ current.end_number }}.

      {% for shard in current.members %}
      ## {{ shard.node_id }} (status: {{ nodes[shard.node_id].status }})
      {{ nodes[shard.node_id].output or "(no output)" }}

      {% endfor %}
```

The bundled `codex-fuzz-batched` scaffold turns that into a ready-to-run 128-shard Codex reference and scales further with `agentflow init fuzz-batched-256.yaml --template codex-fuzz-batched --set shards=256 --set batch_size=32 --set concurrency=64`.

When a mostly-regular matrix needs a few real-world adjustments, use `fanout.exclude` and `fanout.include` before moving all the way to a CSV catalog:

```yaml
nodes:
  - id: fuzzer
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

When the shard catalog or matrix axes need to live outside the main pipeline file, use `fanout.values_path` or `fanout.matrix_path`. `values_path` accepts JSON/YAML lists and CSV files; `matrix_path` accepts JSON/YAML objects. Relative paths resolve from the pipeline file, which keeps large maintainer-owned catalogs easy to retarget without rewriting the reducer or launch settings. The bundled `codex-fuzz-matrix-manifest` scaffold renders this pattern with a sidecar axes file, and `agentflow init fuzz-matrix-manifest-128.yaml --template codex-fuzz-matrix-manifest --set bucket_count=8 --set concurrency=32` scales it to a full 128-shard campaign without hand-editing the manifest. When staged reducers should mirror unique fields already present on the shard fanout, use `codex-fuzz-hierarchical-grouped`, which keeps only the axes manifest and derives the reducer roster via `fanout.group_by`. When the same staged-reducer pattern should start from a CSV catalog instead of reusable axes, use `codex-fuzz-catalog-grouped`, which keeps explicit per-row metadata in a spreadsheet-friendly manifest while still giving each reducer scoped shard ids and members. When you need staged reducers with an explicitly maintainer-owned roster that can diverge from the shard axes, use `codex-fuzz-hierarchical-manifest`, which renders both the axes manifest and the reducer family roster from one scaffold. CSV-backed catalogs are especially useful when you truly need explicit per-row metadata that cannot be derived cleanly from reusable axes.

```yaml
nodes:
  - id: fuzzer
    fanout:
      as: shard
      matrix_path: manifests/campaign.axes.yaml
    agent: codex
    prompt: |
      Fuzz {{ shard.target }} with {{ shard.sanitizer }} using seed {{ shard.seed }}.
```

See `examples/codex-fanout-repo-sweep.yaml` for a bundled maintainer-friendly review template, `examples/codex-repo-sweep-batched.yaml` for the corresponding 128-shard batched maintainer sweep that showcases `node_defaults`, `agent_defaults`, and `fanout.batches`, `examples/fuzz/codex-fuzz-matrix.yaml` for a baseline `fanout.matrix` fuzz starter, `examples/fuzz/codex-fuzz-matrix-derived.yaml` for the corresponding `fanout.derive` pattern with reusable labels and workdirs, `examples/fuzz/codex-fuzz-matrix-curated.yaml` for the `fanout.exclude` / `fanout.include` pattern that tunes a matrix without a sidecar catalog, `examples/fuzz/codex-fuzz-matrix-128.yaml` for a 128-shard inline matrix reference, `examples/fuzz/codex-fuzz-hierarchical-grouped.yaml` for the staged-reducer scaffold that derives reducer families from the expanded shard fanout, `examples/fuzz/codex-fuzz-hierarchical-manifest.yaml` for the configurable staged-reducer scaffold with sidecar axes and family manifests, `examples/fuzz/codex-fuzz-hierarchical-128.yaml` for the fixed 128-shard staged reducer reference, `examples/fuzz/codex-fuzz-matrix-manifest.yaml` for the configurable manifest-backed scaffold, `examples/fuzz/codex-fuzz-matrix-manifest-128.yaml` for the fixed 128-shard manifest-backed reference, `examples/fuzz/codex-fuzz-catalog.yaml` for a 128-shard CSV-backed shard catalog, `examples/fuzz/codex-fuzz-catalog-grouped.yaml` for the corresponding CSV-backed staged-reducer scaffold, `examples/fuzz/codex-fuzz-batched.yaml` for the batched 128-shard homogeneous reference, `examples/fuzz/fuzz_codex_32.yaml` for the default right-sized Codex fuzz swarm, and `examples/fuzz/fuzz_codex_128.yaml` for the fixed 128-shard homogeneous reference swarm. The maintainers' sweep starters are scaffoldable via `agentflow init --template codex-fanout-repo-sweep` and `agentflow init repo-sweep-batched.yaml --template codex-repo-sweep-batched`, while the fuzz starters are scaffoldable via `agentflow init --template codex-fuzz-matrix`, `agentflow init --template codex-fuzz-matrix-derived`, `agentflow init --template codex-fuzz-matrix-curated`, `agentflow init --template codex-fuzz-matrix-128`, `agentflow init fuzz-hierarchical-grouped.yaml --template codex-fuzz-hierarchical-grouped`, `agentflow init fuzz-hierarchical-grouped-128.yaml --template codex-fuzz-hierarchical-grouped --set bucket_count=8 --set concurrency=32`, `agentflow init fuzz-hierarchical.yaml --template codex-fuzz-hierarchical-manifest`, `agentflow init fuzz-hierarchical-128.yaml --template codex-fuzz-hierarchical-manifest --set bucket_count=8 --set concurrency=32`, `agentflow init --template codex-fuzz-hierarchical-128`, `agentflow init fuzz-matrix-manifest.yaml --template codex-fuzz-matrix-manifest`, `agentflow init fuzz-matrix-manifest-128.yaml --template codex-fuzz-matrix-manifest --set bucket_count=8 --set concurrency=32`, `agentflow init fuzz-catalog.yaml --template codex-fuzz-catalog`, `agentflow init fuzz-catalog-grouped.yaml --template codex-fuzz-catalog-grouped`, `agentflow init fuzz-batched.yaml --template codex-fuzz-batched`, `agentflow init --template codex-fuzz-swarm`, and `agentflow init --template codex-fuzz-swarm --set shards=128 --set concurrency=32`.

## Docs

- [Docs index](docs/README.md)
- [Examples guide](docs/examples.md)
- [CLI and operations](docs/cli.md)
- [Pipeline reference](docs/pipelines.md)
- [Testing and maintainer workflows](docs/testing.md)
- [Background and sources](docs/background.md)
