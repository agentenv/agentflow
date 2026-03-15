# Examples Guide

Use this page to choose a starter without reading every bundled YAML file.

## Maintainer sweeps

| Starter | Use it when | Key features |
| --- | --- | --- |
| `codex-fanout-repo-sweep` | You want a small repo review fanout and a simple merge. | 8 Codex shards, one planning step, one final reducer. |
| `codex-repo-sweep-batched` | You want a large repo audit or review sweep that should scale to 64 or 128 shards without producing one unreadable final merge. | `node_defaults`, `agent_defaults`, `fanout.batches`, staged reducers. |

## Fuzzing swarms

| Starter | Use it when | Key features |
| --- | --- | --- |
| `codex-fuzz-swarm` | The campaign is homogeneous and you only need a single final reducer. | `fanout.count`, shared init, resize with `--set shards=...`. |
| `codex-fuzz-batched` | The campaign is homogeneous, but a single reducer would be too noisy. | `fanout.batches`, scoped intermediate reducers. |
| `codex-fuzz-matrix` | The shard roster is a clean cartesian product. | `fanout.matrix`. |
| `codex-fuzz-matrix-derived` | The matrix also needs reusable labels or workdirs. | `fanout.derive`. |
| `codex-fuzz-matrix-curated` | The matrix is mostly regular, but you need a few exclusions or bespoke shards. | `fanout.exclude`, `fanout.include`, `fanout.derive`. |
| `codex-fuzz-matrix-manifest` | The matrix axes should live outside the main pipeline file. | `fanout.matrix_path`, rendered support manifest. |
| `codex-fuzz-catalog` | Every shard needs explicit per-row metadata that is awkward to derive. | `fanout.values_path`, rendered CSV catalog. |
| `codex-fuzz-hierarchical-grouped` | Reducer families should be derived automatically from shard metadata. | `fanout.group_by`, scoped reducers from the shard fanout. |
| `codex-fuzz-hierarchical-manifest` | Reducer families should stay in a maintainer-owned sidecar roster. | `fanout.matrix_path` plus `values_path`. |

The fixed `*-128` examples are reference snapshots when you want to inspect a full large DAG directly from the repo instead of rendering one with `agentflow init`.

## Local smoke flows

| Starter | Use it when | Key features |
| --- | --- | --- |
| `local-kimi-smoke` | You want the shortest real-agent local smoke path. | `bootstrap: kimi`. |
| `local-kimi-shell-init-smoke` | You want the explicit shell-init equivalent of the bundled smoke. | `shell: bash`, `shell_login`, `shell_interactive`, `shell_init: kimi`. |
| `local-kimi-shell-wrapper-smoke` | You want the same flow expressed as an explicit shell wrapper. | `target.shell` wrapper with `{command}` injection. |

## Practical rules

- Start with `codex-fanout-repo-sweep` for small repo reviews and move to `codex-repo-sweep-batched` once you need 32+ Codex workers or staged reducers.
- Start with `codex-fuzz-swarm` for homogeneous campaigns, then move to `codex-fuzz-batched` when the final reducer becomes too large to read.
- Use the matrix starters when shard metadata is derivable, and use the catalog starters when every shard needs explicit maintainer-owned metadata.
- Prefer the manifest-backed starters when the shard roster or reducer roster should live in sidecar files that non-authors can edit.
