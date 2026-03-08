from agentflow import DAG, claude, codex, kimi


def test_airflow_like_dag_builds_dependencies():
    with DAG("demo", working_dir="/tmp/work", concurrency=2) as dag:
        plan = codex(task_id="plan", prompt="plan")
        implement = claude(task_id="implement", prompt="implement")
        review = kimi(task_id="review", prompt="review")
        merge = codex(task_id="merge", prompt="merge")
        plan >> [implement, review]
        implement >> merge
        review >> merge

    spec = dag.to_spec()
    nodes = spec.node_map
    assert spec.name == "demo"
    assert spec.working_dir == "/tmp/work"
    assert nodes["implement"].depends_on == ["plan"]
    assert nodes["review"].depends_on == ["plan"]
    assert set(nodes["merge"].depends_on) == {"implement", "review"}


def test_airflow_like_dag_applies_local_target_defaults():
    with DAG(
        "local-defaults",
        local_target_defaults={
            "shell": "bash",
            "shell_login": True,
            "shell_interactive": True,
            "shell_init": ["command -v kimi >/dev/null 2>&1", "kimi"],
        },
    ) as dag:
        codex(task_id="plan", prompt="plan")
        claude(task_id="review", prompt="review", target={"cwd": "review-work"})

    spec = dag.to_spec()

    assert spec.local_target_defaults is not None
    assert spec.nodes[0].target.shell == "bash"
    assert spec.nodes[0].target.shell_init == ["command -v kimi >/dev/null 2>&1", "kimi"]
    assert spec.nodes[1].target.shell == "bash"
    assert spec.nodes[1].target.cwd == "review-work"
