# Examples Guide

## Bundled Templates

| Template | Use it when | Key features |
| --- | --- | --- |
| `pipeline` | You want the smallest generic starter. | Codex plan, Claude implementation, Kimi review, final Codex merge. |
| `codex-repo-sweep-batched` | You want a large repo audit that still produces a readable handoff. | `fanout`, `merge`, `node_defaults`, `agent_defaults`, staged reducers. |
| `local-kimi-smoke` | You want the shortest real-agent local smoke path. | `bootstrap: kimi`. |
| `local-kimi-shell-init-smoke` | You want the explicit shell-init equivalent. | `shell: bash`, login shell flags, `shell_init: kimi`. |
| `local-kimi-shell-wrapper-smoke` | You want the bootstrap expressed as an explicit wrapper. | `target.shell` with `{command}` injection. |

## Python Examples

| Example | Use it when | Key features |
| --- | --- | --- |
| `airflow_like.py` | You want the smallest Python-authored DAG reference. | Static dependencies with `plan >> [implement, review]`. |
| `airflow_like_fuzz_batched.py` | You want a large shard campaign driven by count fanout, batch merge, and a periodic monitor. | `fanout(node, 128)`, `merge(node, src, size=16)`, `schedule.every_seconds`. |
| `airflow_like_fuzz_grouped.py` | You want a large shard campaign driven by matrix fanout and grouped merge. | `fanout(node, {...})`, `merge(node, src, by=[...])`. |
| `contract_audit.py` | You want a public example of an AgentFlow native `EVM + Solidity + Foundry` contract audit. | Manifest-driven intake, fixed audit tracks, source-ground review, PoC verification, deterministic reporting. |
