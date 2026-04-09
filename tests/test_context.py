from __future__ import annotations

from pathlib import Path

from agentflow.context import build_render_context, render_node_prompt
from agentflow.loader import load_pipeline_from_data
from agentflow.specs import NodeResult, NodeStatus


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fanout_pipeline(tmp_path: Path):
    return load_pipeline_from_data(
        {
            "name": "fanout-context",
            "working_dir": str(tmp_path),
            "nodes": [
                {
                    "id": "worker",
                    "fanout": {
                        "as": "shard",
                        "values": [
                            {"target": "libpng", "seed": 1001},
                            {"target": "sqlite", "seed": 2002},
                            {"target": "openssl", "seed": 3003},
                        ],
                    },
                    "agent": "codex",
                    "prompt": "worker {{ shard.target }} seed {{ shard.seed }}",
                },
                {
                    "id": "merge",
                    "agent": "codex",
                    "depends_on": ["worker"],
                    "prompt": (
                        "completed={{ fanouts.worker.summary.completed }}/{{ fanouts.worker.size }} "
                        "failed={{ fanouts.worker.summary.failed }} :: "
                        "{% for shard in fanouts.worker.with_output.nodes %}"
                        "{{ shard.id }}={{ shard.target }}:{{ shard.output }};"
                        "{% endfor %}"
                    ),
                },
            ],
        },
        base_dir=tmp_path,
    )


def _batched_pipeline(tmp_path: Path):
    return load_pipeline_from_data(
        {
            "name": "batched-context",
            "working_dir": str(tmp_path),
            "nodes": [
                {
                    "id": "worker",
                    "fanout": {
                        "count": 3,
                        "as": "shard",
                        "derive": {
                            "workspace": "agents/agent_{{ shard.suffix }}",
                        },
                    },
                    "agent": "codex",
                    "prompt": "worker {{ shard.number }}",
                },
                {
                    "id": "batch_merge",
                    "fanout": {
                        "as": "batch",
                        "batches": {
                            "from": "worker",
                            "size": 2,
                        },
                    },
                    "agent": "codex",
                    "depends_on": ["worker"],
                    "prompt": (
                        "batch={{ item.number }}/{{ item.count }} "
                        "range={{ item.start_number }}-{{ item.end_number }} "
                        "done={{ item.scope.summary.completed }} "
                        "failed={{ item.scope.summary.failed }} "
                        "ids={{ item.scope.ids | join(',') }} :: "
                        "{% for shard in item.scope.with_output.nodes %}"
                        "{{ shard.node_id }}@{{ shard.workspace }}={{ shard.output }};"
                        "{% endfor %}"
                    ),
                },
            ],
        },
        base_dir=tmp_path,
    )


def _grouped_pipeline(tmp_path: Path):
    return load_pipeline_from_data(
        {
            "name": "grouped-context",
            "working_dir": str(tmp_path),
            "nodes": [
                {
                    "id": "worker",
                    "fanout": {
                        "as": "shard",
                        "values": [
                            {"target": "libpng", "seed": 1001, "workspace": "agents/libpng_0"},
                            {"target": "libpng", "seed": 1002, "workspace": "agents/libpng_1"},
                            {"target": "sqlite", "seed": 2002, "workspace": "agents/sqlite_2"},
                        ],
                    },
                    "agent": "codex",
                    "prompt": "worker {{ shard.target }} seed {{ shard.seed }}",
                },
                {
                    "id": "family_merge",
                    "fanout": {
                        "as": "family",
                        "group_by": {
                            "from": "worker",
                            "fields": ["target"],
                        },
                    },
                    "agent": "codex",
                    "depends_on": ["worker"],
                    "prompt": (
                        "family={{ item.target }} ids={{ item.scope.ids | join(',') }} "
                        "done={{ item.scope.summary.completed }} "
                        "failed={{ item.scope.summary.failed }} :: "
                        "{% for shard in item.scope.with_output.nodes %}"
                        "{{ shard.node_id }}@{{ shard.seed }}={{ shard.output }};"
                        "{% endfor %}"
                    ),
                },
            ],
        },
        base_dir=tmp_path,
    )


def test_build_render_context_exposes_fanout_status_and_output_subsets(tmp_path: Path):
    pipeline = _fanout_pipeline(tmp_path)
    results = {
        "worker_0": NodeResult(node_id="worker_0", status=NodeStatus.COMPLETED, output="ok libpng"),
        "worker_1": NodeResult(node_id="worker_1", status=NodeStatus.FAILED, output="retry sqlite"),
        "worker_2": NodeResult(node_id="worker_2", status=NodeStatus.COMPLETED, output=""),
        "merge": NodeResult(node_id="merge"),
    }

    context = build_render_context(pipeline, results)
    worker = context["fanouts"]["worker"]

    assert worker["size"] == 3
    assert worker["summary"]["total"] == 3
    assert worker["summary"]["completed"] == 2
    assert worker["summary"]["failed"] == 1
    assert worker["summary"]["with_output"] == 2
    assert worker["summary"]["without_output"] == 1
    assert worker["status_counts"]["completed"] == 2
    assert worker["status_counts"]["failed"] == 1
    assert [node["id"] for node in worker["completed"]["nodes"]] == ["worker_0", "worker_2"]
    assert [node["id"] for node in worker["failed"]["nodes"]] == ["worker_1"]
    assert [node["id"] for node in worker["with_output"]["nodes"]] == ["worker_0", "worker_1"]
    assert [node["id"] for node in worker["without_output"]["nodes"]] == ["worker_2"]
    assert worker["failed"]["nodes"][0]["target"] == "sqlite"
    assert worker["completed"]["nodes"][1]["seed"] == 3003


def test_render_node_prompt_can_use_fanout_summary_and_filtered_nodes(tmp_path: Path):
    pipeline = _fanout_pipeline(tmp_path)
    results = {
        "worker_0": NodeResult(node_id="worker_0", status=NodeStatus.COMPLETED, output="ok libpng"),
        "worker_1": NodeResult(node_id="worker_1", status=NodeStatus.FAILED, output="retry sqlite"),
        "worker_2": NodeResult(node_id="worker_2", status=NodeStatus.COMPLETED, output=""),
        "merge": NodeResult(node_id="merge"),
    }

    rendered = render_node_prompt(pipeline, pipeline.node_map["merge"], results)

    assert rendered == (
        "completed=2/3 failed=1 :: "
        "worker_0=libpng:ok libpng;"
        "worker_1=sqlite:retry sqlite;"
    )


def test_build_render_context_exposes_current_node_metadata_for_runtime_reducers(tmp_path: Path):
    pipeline = _batched_pipeline(tmp_path)
    results = {
        "worker_0": NodeResult(node_id="worker_0", status=NodeStatus.COMPLETED, output="alpha"),
        "worker_1": NodeResult(node_id="worker_1", status=NodeStatus.FAILED, output="beta"),
        "worker_2": NodeResult(node_id="worker_2", status=NodeStatus.COMPLETED, output=""),
        "batch_merge_0": NodeResult(node_id="batch_merge_0"),
        "batch_merge_1": NodeResult(node_id="batch_merge_1"),
    }

    context = build_render_context(pipeline, results, current_node=pipeline.node_map["batch_merge_0"])

    current = context["item"]
    assert "current" not in context
    assert current["id"] == "batch_merge_0"
    assert current["agent"] == "codex"
    assert current["depends_on"] == ["worker_0", "worker_1"]
    assert current["fanout_group"] == "batch_merge"
    assert current["member_ids"] == ["worker_0", "worker_1"]
    assert current["start_number"] == 1
    assert current["end_number"] == 2
    assert current["scope"]["ids"] == ["worker_0", "worker_1"]
    assert current["scope"]["summary"]["completed"] == 1
    assert current["scope"]["summary"]["failed"] == 1
    assert current["scope"]["summary"]["with_output"] == 2
    assert [node["node_id"] for node in current["scope"]["with_output"]["nodes"]] == ["worker_0", "worker_1"]
    assert current["scope"]["failed"]["nodes"][0]["workspace"] == "agents/agent_1"
    assert current["scope"]["with_output"]["nodes"][0]["output"] == "alpha"


def test_render_node_prompt_can_use_current_node_and_batch_members(tmp_path: Path):
    pipeline = _batched_pipeline(tmp_path)
    results = {
        "worker_0": NodeResult(node_id="worker_0", status=NodeStatus.COMPLETED, output="alpha"),
        "worker_1": NodeResult(node_id="worker_1", status=NodeStatus.FAILED, output="beta"),
        "worker_2": NodeResult(node_id="worker_2", status=NodeStatus.COMPLETED, output=""),
        "batch_merge_0": NodeResult(node_id="batch_merge_0"),
        "batch_merge_1": NodeResult(node_id="batch_merge_1"),
    }

    rendered = render_node_prompt(pipeline, pipeline.node_map["batch_merge_0"], results)

    assert rendered == (
        "batch=1/2 range=1-2 done=1 failed=1 ids=worker_0,worker_1 :: "
        "worker_0@agents/agent_0=alpha;"
        "worker_1@agents/agent_1=beta;"
    )


def test_build_render_context_exposes_current_node_metadata_for_grouped_reducers(tmp_path: Path):
    pipeline = _grouped_pipeline(tmp_path)
    results = {
        "worker_0": NodeResult(node_id="worker_0", status=NodeStatus.COMPLETED, output="alpha"),
        "worker_1": NodeResult(node_id="worker_1", status=NodeStatus.FAILED, output="beta"),
        "worker_2": NodeResult(node_id="worker_2", status=NodeStatus.COMPLETED, output="gamma"),
        "family_merge_0": NodeResult(node_id="family_merge_0"),
        "family_merge_1": NodeResult(node_id="family_merge_1"),
    }

    context = build_render_context(pipeline, results, current_node=pipeline.node_map["family_merge_0"])

    current = context["item"]
    assert "current" not in context
    assert current["id"] == "family_merge_0"
    assert current["agent"] == "codex"
    assert current["depends_on"] == ["worker_0", "worker_1"]
    assert current["fanout_group"] == "family_merge"
    assert current["target"] == "libpng"
    assert current["member_ids"] == ["worker_0", "worker_1"]
    assert current["scope"]["ids"] == ["worker_0", "worker_1"]
    assert current["scope"]["summary"]["completed"] == 1
    assert current["scope"]["summary"]["failed"] == 1
    assert current["scope"]["summary"]["with_output"] == 2
    assert [node["seed"] for node in current["scope"]["with_output"]["nodes"]] == [1001, 1002]
    assert current["scope"]["failed"]["nodes"][0]["node_id"] == "worker_1"
    assert current["scope"]["with_output"]["nodes"][1]["output"] == "beta"


def test_render_node_prompt_can_use_current_node_and_group_members(tmp_path: Path):
    pipeline = _grouped_pipeline(tmp_path)
    results = {
        "worker_0": NodeResult(node_id="worker_0", status=NodeStatus.COMPLETED, output="alpha"),
        "worker_1": NodeResult(node_id="worker_1", status=NodeStatus.FAILED, output="beta"),
        "worker_2": NodeResult(node_id="worker_2", status=NodeStatus.COMPLETED, output="gamma"),
        "family_merge_0": NodeResult(node_id="family_merge_0"),
        "family_merge_1": NodeResult(node_id="family_merge_1"),
    }

    rendered = render_node_prompt(pipeline, pipeline.node_map["family_merge_0"], results)

    assert rendered == (
        "family=libpng ids=worker_0,worker_1 done=1 failed=1 :: "
        "worker_0@1001=alpha;"
        "worker_1@1002=beta;"
    )


def test_current_node_context_preserves_runtime_identity_when_member_keys_conflict(tmp_path: Path):
    pipeline = load_pipeline_from_data(
        {
            "name": "fanout-current-collision",
            "working_dir": str(tmp_path),
            "nodes": [
                {
                    "id": "worker",
                    "fanout": {
                        "as": "shard",
                        "values": [
                            {
                                "id": "manifest-id",
                                "agent": "manifest-agent",
                                "depends_on": ["manifest-dependency"],
                                "target": "libpng",
                            }
                        ],
                    },
                    "agent": "codex",
                    "prompt": "worker {{ shard.target }}",
                }
            ],
        },
        base_dir=tmp_path,
    )
    results = {"worker_0": NodeResult(node_id="worker_0")}

    context = build_render_context(pipeline, results, current_node=pipeline.node_map["worker_0"])
    assert "current" not in context

    assert context["item"]["id"] == "worker_0"
    assert context["item"]["agent"] == "codex"
    assert context["item"]["depends_on"] == []
    assert context["item"]["target"] == "libpng"
    assert context["item"]["value"] == {
        "id": "manifest-id",
        "agent": "manifest-agent",
        "depends_on": ["manifest-dependency"],
        "target": "libpng",
    }


def test_build_render_context_exposes_artifact_paths_and_tick_metadata_for_periodic_nodes(tmp_path: Path):
    pipeline = load_pipeline_from_data(
        {
            "name": "periodic-context",
            "working_dir": str(tmp_path),
            "nodes": [
                {
                    "id": "worker",
                    "fanout": {
                        "count": 2,
                        "as": "shard",
                    },
                    "agent": "codex",
                    "prompt": "worker {{ shard.number }}",
                },
                {
                    "id": "monitor",
                    "agent": "codex",
                    "schedule": {
                        "every_seconds": 600,
                        "until_fanout_settles_from": "worker",
                    },
                    "prompt": "monitor",
                },
            ],
        },
        base_dir=tmp_path,
    )
    results = {
        "worker_0": NodeResult(node_id="worker_0", status=NodeStatus.RUNNING, output="alpha"),
        "worker_1": NodeResult(node_id="worker_1", status=NodeStatus.PENDING),
        "monitor": NodeResult(node_id="monitor", status=NodeStatus.RUNNING, tick_count=2),
    }

    context = build_render_context(
        pipeline,
        results,
        current_node=pipeline.node_map["monitor"],
        run_id="run123",
        artifacts_base_dir=tmp_path / ".agentflow" / "runs",
        current_tick_number=2,
        current_tick_started_at="2026-03-15T12:00:00+00:00",
    )

    assert context["nodes"]["worker_0"]["artifacts"]["stdout_log"].endswith("/run123/artifacts/worker_0/stdout.log")
    assert context["fanouts"]["worker"]["nodes"][0]["artifacts"]["stderr_log"].endswith(
        "/run123/artifacts/worker_0/stderr.log"
    )
    assert "current" not in context
    assert context["item"]["schedule"]["every_seconds"] == 600
    assert context["item"]["schedule"]["until_fanout_settles_from"] == "worker"
    assert context["item"]["tick_number"] == 2
    assert context["item"]["tick_started_at"] == "2026-03-15T12:00:00+00:00"


def test_render_node_prompt_can_use_artifact_paths_and_tick_metadata(tmp_path: Path):
    pipeline = load_pipeline_from_data(
        {
            "name": "periodic-render",
            "working_dir": str(tmp_path),
            "nodes": [
                {
                    "id": "worker",
                    "fanout": {
                        "count": 1,
                        "as": "shard",
                    },
                    "agent": "codex",
                    "prompt": "worker {{ shard.number }}",
                },
                {
                    "id": "monitor",
                    "agent": "codex",
                    "schedule": {
                        "every_seconds": 600,
                        "until_fanout_settles_from": "worker",
                    },
                    "prompt": (
                        "tick={{ item.tick_number }} "
                        "stdout={{ fanouts.worker.nodes[0].artifacts.stdout_log }}"
                    ),
                },
            ],
        },
        base_dir=tmp_path,
    )
    results = {
        "worker_0": NodeResult(node_id="worker_0", status=NodeStatus.RUNNING, output="alpha"),
        "monitor": NodeResult(node_id="monitor", status=NodeStatus.RUNNING, tick_count=1),
    }

    rendered = render_node_prompt(
        pipeline,
        pipeline.node_map["monitor"],
        results,
        run_id="run123",
        artifacts_base_dir=tmp_path / ".agentflow" / "runs",
        current_tick_number=1,
        current_tick_started_at="2026-03-15T12:00:00+00:00",
    )

    assert rendered.endswith("/run123/artifacts/worker_0/stdout.log")
    assert rendered.startswith("tick=1 ")


def test_render_node_prompt_threads_target_skill_policy_to_package_resolution(tmp_path: Path, monkeypatch):
    target_repo = tmp_path / "target-repo"
    target_package_root = target_repo / ".agents" / "skills" / "static-analysis"
    _write(target_package_root / "SKILL.md", "# Target Wrapper")
    _write(target_package_root / "skills" / "semgrep" / "SKILL.md", "# Target Semgrep")

    owned_root = tmp_path / "agentflow-owned" / ".agents" / "skills"
    monkeypatch.setattr("agentflow.skill_roots.owned_skill_package_roots", lambda: (owned_root,))

    pipeline = load_pipeline_from_data(
        {
            "name": "trusted-target-skills-render",
            "working_dir": str(target_repo),
            "nodes": [
                {
                    "id": "plan",
                    "agent": "codex",
                    "prompt": "Summarize the repo.",
                    "skills": ["static-analysis::semgrep"],
                    "target_skill_policy": "inherit_all",
                }
            ],
        },
        base_dir=tmp_path,
    )

    prompt = render_node_prompt(pipeline, pipeline.nodes[0], {"plan": NodeResult(node_id="plan")})

    assert "Selected skills:" in prompt
    assert "# Target Semgrep" in prompt
    assert prompt.endswith("Task:\nSummarize the repo.")
