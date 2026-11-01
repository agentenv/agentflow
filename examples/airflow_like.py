from agentflow import Graph, claude, codex, kimi


with Graph("airflow-like-example", working_dir=".", concurrency=3) as dag:
    plan = codex(
        task_id="plan",
        prompt="Inspect the repo and produce a concise plan.",
        model="gpt-5-codex",
        tools="read_only",
    )
    implement = claude(
        task_id="implement",
        prompt="Implement the approved plan:\n\n{{ nodes.plan.output }}",
        model="claude-sonnet-4-5",
        tools="read_write",
    )
    review = kimi(
        task_id="review",
        prompt="Review the plan and call out risks:\n\n{{ nodes.plan.output }}",
        model="kimi-k2-turbo-preview",
        capture="trace",
        tools="read_write",  # the Kimi CLI cannot restrict tools, so Kimi nodes must declare read_write
    )
    merge = codex(
        task_id="merge",
        prompt=(
            "Merge the implementation and review into one final response.\n\n"
            "Implementation:\n{{ nodes.implement.output }}\n\n"
            "Review:\n{{ nodes.review.output }}"
        ),
        model="gpt-5-codex",
    )

    plan >> [implement, review]
    [implement, review] >> merge

print(dag.to_json())
