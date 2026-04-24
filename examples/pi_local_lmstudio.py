"""Pi agent example: local LMStudio for parallel scanning, Codex for synthesis.

Fans a scan out across several files using the ``pi`` CLI pointed at a locally
running LMStudio endpoint, then hands the merged findings to Codex for a
final review. Assumes the user's ``~/.pi/agent/models.json`` already declares
an ``lmstudio`` provider (the default when LMStudio is installed). Swap the
``model`` kwarg for whichever model you have loaded in LMStudio.
"""

from agentflow import Graph, codex, fanout, merge, pi


with Graph("pi-lmstudio-scan", working_dir=".", concurrency=4) as dag:
    scans = fanout(
        pi(
            task_id="scan",
            prompt=(
                "Read {{ item.path }} and extract every TODO/FIXME/HACK with 1 line "
                "of context. If none, reply with 'NONE'."
            ),
            # Pi's `:low|:medium|:high|:off` suffix is only honored by models
            # that expose granular reasoning. Qwen served via LMStudio accepts
            # only `on`/`off`, so non-off levels silently collapse to `on`.
            # To bound runaway chain-of-thought, lower `maxTokens` in
            # `~/.pi/agent/models.json` or use `:off` to skip reasoning.
            model="lmstudio/qwen/qwen3.6-27b",
            tools="read_only",
        ),
        [
            {"path": "agentflow/dsl.py"},
            {"path": "agentflow/orchestrator.py"},
            {"path": "agentflow/specs.py"},
            {"path": "agentflow/traces.py"},
        ],
    )

    summary = merge(
        codex(
            task_id="summary",
            prompt=(
                "Consolidate these scan results into a single punch list, grouped by urgency.\n\n"
                "{% for r in fanouts.scan.nodes %}{{ r.output }}\n---\n{% endfor %}"
            ),
            model="gpt-5-codex",
        ),
        scans,
        size=4,
    )


print(dag.to_json())
