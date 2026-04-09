from __future__ import annotations

from agentflow.observation import summarize_command_result, update_observation_state
from agentflow.specs import AgentKind, NodeResult, NormalizedTraceEvent, PipelineSpec, RunEvent, RunRecord, RunStatus


def _record() -> RunRecord:
    pipeline = PipelineSpec.model_validate(
        {
            "name": "demo",
            "working_dir": ".",
            "nodes": [{"id": "worker", "agent": "codex", "prompt": "hi"}],
        }
    )
    return RunRecord(
        id="run1",
        status=RunStatus.RUNNING,
        pipeline=pipeline,
        nodes={"worker": NodeResult(node_id="worker")},
    )


def test_node_result_exposes_observation_fields():
    node = NodeResult(node_id="worker")

    assert node.last_event_at is None
    assert node.last_progress_kind is None
    assert node.last_progress_message is None
    assert node.last_command is None
    assert node.last_command_status is None
    assert node.stale_since is None
    assert node.progress_count == 0


def test_command_execution_updates_last_command_and_progress_message():
    trace = NormalizedTraceEvent(
        node_id="worker",
        agent=AgentKind.CODEX,
        kind="item_started",
        title="Item started: command_execution",
        raw={
            "item": {
                "type": "command_execution",
                "command": "pytest cli/tests/cli/test_file_cli.py",
            }
        },
    )
    record = _record()

    update_observation_state(
        record,
        RunEvent(
            run_id="run1",
            type="node_trace",
            node_id="worker",
            data={"trace": trace.model_dump(mode="json")},
        ),
    )

    node = record.nodes["worker"]
    assert node.last_command == "pytest cli/tests/cli/test_file_cli.py"
    assert node.last_command_status == "running"
    assert node.last_progress_kind == "command_started"
    assert "pytest" in node.last_progress_message
    assert record.last_progress_at is not None
    assert record.active_node_ids == ["worker"]


def test_command_completion_summarizes_pytest_output():
    summary = summarize_command_result(
        "pytest cli/tests/cli/test_file_cli.py",
        0,
        "===================\n3 passed in 0.39s\n",
    )

    assert summary == "pytest cli/tests/cli/test_file_cli.py -> 3 passed"


def test_observation_marks_active_node_stale_after_threshold():
    record = _record()
    node = record.nodes["worker"]
    node.last_progress_at = "2026-04-02T13:00:00+00:00"

    update_observation_state(
        record,
        RunEvent(run_id="run1", type="run_started"),
        now="2026-04-02T13:06:00+00:00",
        stale_after_seconds=300,
    )

    assert node.stale_since == "2026-04-02T13:06:00+00:00"
    assert record.stale_node_ids == ["worker"]
