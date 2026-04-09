from __future__ import annotations

import asyncio

import pytest

from agentflow.follow import follow_run_events
from agentflow.specs import PipelineSpec, RunEvent, RunRecord
from agentflow.store import RunStore
from agentflow.terminal_progress import TerminalProgressRenderer


def test_terminal_renderer_summarizes_pytest_command_completion():
    renderer = TerminalProgressRenderer()

    lines = renderer.render_progress(
        node_id="worker",
        kind="command_completed",
        message="pytest cli/tests/cli/test_file_cli.py -> 3 passed",
        timestamp="2026-04-02T13:00:00+00:00",
    )

    assert lines == [
        "[13:00:00] worker: pytest cli/tests/cli/test_file_cli.py -> 3 passed"
    ]


def test_terminal_renderer_deduplicates_repeated_progress_messages():
    renderer = TerminalProgressRenderer()

    first = renderer.render_progress(
        node_id="worker",
        kind="agent_message",
        message="checking managed-agents surfaces",
        timestamp="2026-04-02T13:00:00+00:00",
    )
    second = renderer.render_progress(
        node_id="worker",
        kind="agent_message",
        message="checking managed-agents surfaces",
        timestamp="2026-04-02T13:00:01+00:00",
    )

    assert first == ["[13:00:00] worker: checking managed-agents surfaces"]
    assert second == []


@pytest.mark.asyncio
async def test_follow_run_events_replays_existing_events_and_streams_new_ones(tmp_path):
    pipeline = PipelineSpec.model_validate(
        {
            "name": "follow",
            "working_dir": str(tmp_path),
            "nodes": [{"id": "alpha", "agent": "codex", "prompt": "hi"}],
        }
    )
    store = RunStore(tmp_path / "runs")
    await store.create_run(RunRecord(id="run-1", pipeline=pipeline))
    await store.append_event("run-1", RunEvent(run_id="run-1", type="run_started"))

    seen: list[str] = []

    async def consume() -> None:
        async for event in follow_run_events(store, "run-1", poll_interval=0.01):
            seen.append(event.type)
            if event.type == "run_completed":
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    await store.append_event("run-1", RunEvent(run_id="run-1", type="node_started", node_id="alpha"))
    record = store.get_run("run-1")
    record.status = "completed"
    await store.append_event("run-1", RunEvent(run_id="run-1", type="run_completed", data={"status": "completed"}))
    await task

    assert seen == ["run_started", "node_started", "run_completed"]


@pytest.mark.asyncio
async def test_follow_run_events_can_use_in_memory_state_without_refreshing_store(tmp_path):
    class RefreshForbiddenStore(RunStore):
        def refresh_run(self, run_id: str) -> RunRecord:
            raise AssertionError("refresh_run should not be called in same-process follow mode")

    pipeline = PipelineSpec.model_validate(
        {
            "name": "follow-in-memory",
            "working_dir": str(tmp_path),
            "nodes": [{"id": "alpha", "agent": "codex", "prompt": "hi"}],
        }
    )
    store = RefreshForbiddenStore(tmp_path / "runs")
    await store.create_run(RunRecord(id="run-1", pipeline=pipeline))
    await store.append_event("run-1", RunEvent(run_id="run-1", type="run_started"))

    seen: list[str] = []

    async def consume() -> None:
        async for event in follow_run_events(store, "run-1", poll_interval=0.01, refresh_run_state=False):
            seen.append(event.type)
            if event.type == "run_completed":
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    record = store.get_run("run-1")
    record.status = "completed"
    await store.append_event("run-1", RunEvent(run_id="run-1", type="run_completed", data={"status": "completed"}))
    await task

    assert seen == ["run_started", "run_completed"]
