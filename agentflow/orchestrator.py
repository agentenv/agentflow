"""Async pipeline orchestration for AgentFlow runs.

Each submitted run is driven in a background thread that owns an asyncio loop for
scheduling node tasks, persisting state transitions, and reacting to cancellation,
rerun, and periodic-control signals without blocking other runs.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from agentflow.agents.registry import AdapterRegistry, default_adapter_registry
from agentflow.context import render_node_prompt
from agentflow.observation import update_observation_state
from agentflow.prepared import ExecutionPaths, PreparedExecution, build_execution_paths
from agentflow.runners.registry import RunnerRegistry, default_runner_registry
from agentflow.specs import (
    NodeAttempt,
    NodeResult,
    NodeStatus,
    PeriodicActuationMode,
    PipelineSpec,
    RunEvent,
    RunRecord,
    RunStatus,
)
from agentflow.store import RunStore
from agentflow.success import evaluate_success
from agentflow.traces import create_trace_parser
from agentflow.utils import looks_sensitive_key, redact_sensitive_shell_value, utcnow_iso


_TERMINAL_NODE_STATUSES = {
    NodeStatus.COMPLETED,
    NodeStatus.FAILED,
    NodeStatus.SKIPPED,
    NodeStatus.CANCELLED,
}


class _PeriodicAction(BaseModel):
    kind: str
    node_ids: list[str] = Field(default_factory=list)
    reason: str | None = None


class _PeriodicActionEnvelope(BaseModel):
    analysis: str | None = None
    actions: list[_PeriodicAction] = Field(default_factory=list)


@dataclass(slots=True)
class _NodeExecutionOutcome:
    node_id: str
    periodic_tick_number: int | None = None
    periodic_actions: _PeriodicActionEnvelope | None = None
    periodic_action_parse_error: str | None = None


@dataclass(slots=True)
class _PeriodicNodeRuntimeState:
    tick_count: int = 0
    next_tick_at: float | None = None
    last_tick_started_at: str | None = None
    last_tick_started_mono: float | None = None


@dataclass(slots=True)
class Orchestrator:
    """Coordinate pipeline run lifecycles against the persistent run store.

    The orchestrator accepts submissions, starts bounded background workers, and
    advances each run by scheduling ready nodes until the run completes, fails, or
    is cancelled.
    """

    store: RunStore
    adapters: AdapterRegistry = default_adapter_registry
    runners: RunnerRegistry = default_runner_registry
    max_concurrent_runs: int = 2
    _run_slots: threading.Semaphore = field(init=False, repr=False)
    _cancel_flags: dict[str, threading.Event] = field(default_factory=dict, init=False, repr=False)
    _run_finished: dict[str, threading.Event] = field(default_factory=dict, init=False, repr=False)
    _node_cancel_flags: dict[str, set[str]] = field(default_factory=dict, init=False, repr=False)
    _pending_node_reruns: dict[str, set[str]] = field(default_factory=dict, init=False, repr=False)
    _scratchboards: dict[str, "Scratchboard"] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._run_slots = threading.Semaphore(self.max_concurrent_runs)
        self._inject_shared_resource_manager()

    def _inject_shared_resource_manager(self) -> None:
        """Give EC2 and ECS runners a shared resource manager."""
        from agentflow.cloud.shared import SharedResourceManager

        manager = SharedResourceManager()
        for kind in ("ec2", "ecs"):
            try:
                runner = self.runners.get(kind)
                runner._shared_manager = manager
            except KeyError:
                pass

    @staticmethod
    def _reset_node_for_cycle(record: "RunRecord", node_id: str, remaining: set[str]) -> None:
        """Reset a node to PENDING so it can be re-executed in a cycle."""
        node_result = record.nodes.get(node_id)
        if node_result is None:
            return
        node_result.status = NodeStatus.PENDING
        node_result.finished_at = None
        node_result.output = None
        node_result.exit_code = None
        node_result.success = None
        node_result.success_details = []
        remaining.add(node_id)

    @staticmethod
    def _nodes_between(node_map: dict[str, "NodeSpec"], start_id: str, end_id: str) -> list[str]:
        """Find node IDs on the path from start to end (exclusive of both endpoints)."""
        # BFS forward from start following depends_on edges in reverse
        reverse_deps: dict[str, list[str]] = {}
        for nid, node in node_map.items():
            for dep in node.depends_on:
                reverse_deps.setdefault(dep, []).append(nid)

        visited: set[str] = set()
        queue = [start_id]
        while queue:
            current = queue.pop(0)
            for downstream in reverse_deps.get(current, []):
                if downstream == end_id:
                    continue
                if downstream not in visited:
                    visited.add(downstream)
                    queue.append(downstream)
        return [nid for nid in visited if nid != start_id]

    def _register_shared_resources(self, pipeline: "PipelineSpec") -> None:
        """Scan nodes for shared targets and pre-register expected ref counts."""
        from collections import Counter

        shared_counts: Counter[str] = Counter()
        for node in pipeline.nodes:
            shared_id = getattr(node.target, "shared", None)
            if shared_id:
                shared_counts[shared_id] += 1

        if shared_counts:
            for kind in ("ec2", "ecs"):
                try:
                    runner = self.runners.get(kind)
                    mgr = getattr(runner, "_shared_manager", None)
                    if mgr:
                        for sid, count in shared_counts.items():
                            mgr.register_expected(sid, count)
                except KeyError:
                    pass

    async def submit(self, pipeline: PipelineSpec) -> RunRecord:
        """Create a queued run and start its background scheduler when a slot opens.

        Returns the newly created `RunRecord` with all nodes initialized as pending.
        """

        run_id = self.store.new_run_id()
        run = RunRecord(
            id=run_id,
            status=RunStatus.QUEUED,
            pipeline=pipeline,
            nodes={node.id: NodeResult(node_id=node.id, status=NodeStatus.PENDING) for node in pipeline.nodes},
        )
        self._cancel_flags[run_id] = threading.Event()
        self._run_finished[run_id] = threading.Event()
        self._node_cancel_flags[run_id] = set()
        self._pending_node_reruns[run_id] = set()
        await self.store.create_run(run)
        await self._publish(run_id, "run_queued", pipeline=pipeline.model_dump(mode="json"))

        def _background() -> None:
            acquired = False
            try:
                while not acquired:
                    if self._should_cancel(run_id):
                        asyncio.run(self._finalize_cancelled_queue_run(run_id))
                        return
                    acquired = self._run_slots.acquire(timeout=0.1)
                asyncio.run(self.run(run_id))
            finally:
                if acquired:
                    self._run_slots.release()
                self._run_finished[run_id].set()

        threading.Thread(target=_background, name=f"agentflow-{run_id}", daemon=True).start()
        return run

    async def wait(self, run_id: str, timeout: float | None = None) -> RunRecord:
        terminal = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}

        async def _poll() -> RunRecord:
            while True:
                record = self.store.get_run(run_id)
                if record.status in terminal:
                    finished = self._run_finished.get(run_id)
                    if finished is None or finished.is_set():
                        return record
                await asyncio.sleep(0.05)

        if timeout is None:
            return await _poll()
        return await asyncio.wait_for(_poll(), timeout=timeout)

    async def cancel(self, run_id: str) -> RunRecord:
        """Request cancellation for a run.

        Queued runs are finalized immediately; active runs are marked cancelling and
        observed cooperatively by the run loop and executing nodes.
        """

        record = self.store.get_run(run_id)
        flag = self._cancel_flags.setdefault(run_id, threading.Event())
        flag.set()
        await self.store.request_cancel(run_id)
        if record.status == RunStatus.QUEUED:
            await self._finalize_cancelled_queue_run(run_id)
            return self.store.get_run(run_id)
        if record.status in {RunStatus.RUNNING, RunStatus.PENDING}:
            record.status = RunStatus.CANCELLING
            await self._publish(run_id, "run_cancelling")
            await self.store.persist_run(run_id)
        return record

    async def rerun(self, run_id: str) -> RunRecord:
        """Submit a fresh run using the stored pipeline from an existing run.

        Returns the new queued `RunRecord`; prior run state is left unchanged.
        """

        record = self.store.get_run(run_id)
        return await self.submit(record.pipeline)

    def _should_cancel(self, run_id: str) -> bool:
        if self._cancel_flags.get(run_id, threading.Event()).is_set():
            return True
        return self.store.cancel_requested(run_id)

    def _should_cancel_node(self, run_id: str, node_id: str) -> bool:
        return node_id in self._node_cancel_flags.get(run_id, set())

    async def _finalize_cancelled_queue_run(self, run_id: str) -> None:
        record = self.store.get_run(run_id)
        record.status = RunStatus.CANCELLED
        record.finished_at = utcnow_iso()
        for node in record.nodes.values():
            if node.status in {NodeStatus.PENDING, NodeStatus.QUEUED, NodeStatus.READY}:
                node.status = NodeStatus.CANCELLED
                node.finished_at = record.finished_at
        await self._publish(run_id, "run_completed", status=record.status.value)
        await self.store.clear_cancel_request(run_id)
        await self.store.persist_run(run_id)

    def _build_paths(self, pipeline: PipelineSpec, run_id: str, node_id: str, node_target: Any) -> ExecutionPaths:
        return build_execution_paths(
            base_dir=self.store.base_dir,
            pipeline_workdir=pipeline.working_path,
            run_id=run_id,
            node_id=node_id,
            node_target=node_target,
        )

    async def _publish(self, run_id: str, event_type: str, *, node_id: str | None = None, **data: Any) -> None:
        event = RunEvent(run_id=run_id, type=event_type, node_id=node_id, data=data)
        update_observation_state(self.store.get_run(run_id), event)
        await self.store.append_event(run_id, event)
        await self.store.persist_run(run_id)

    async def _publish_trace(self, run_id: str, node_id: str, event) -> None:
        await self.store.append_artifact_text(run_id, node_id, "trace.jsonl", event.model_dump_json() + "\n")
        await self._publish(run_id, "node_trace", node_id=node_id, trace=event.model_dump(mode="json"))

    def _is_sensitive_launch_key(self, key: str) -> bool:
        return looks_sensitive_key(key)

    def _sanitize_launch_value(self, key: str | None, value: Any) -> Any:
        if key and self._is_sensitive_launch_key(key) and value is not None:
            return "<redacted>"
        if isinstance(value, dict):
            if key == "runtime_files":
                return sorted(value)
            return {inner_key: self._sanitize_launch_value(inner_key, inner_value) for inner_key, inner_value in value.items()}
        if isinstance(value, list):
            return [self._sanitize_launch_value(None, item) for item in value]
        return value

    def _launch_artifact_payload(self, attempt_number: int, plan: Any) -> dict[str, Any]:
        return {
            "attempt": attempt_number,
            "kind": plan.kind,
            "command": redact_sensitive_shell_value(list(plan.command)) if plan.command is not None else None,
            "env": self._sanitize_launch_value("env", plan.env),
            "cwd": plan.cwd,
            "stdin": plan.stdin,
            "runtime_files": list(plan.runtime_files),
            "payload": self._sanitize_launch_value("payload", plan.payload),
        }

    async def _write_launch_artifacts(self, run_id: str, node_id: str, attempt_number: int, plan: Any) -> None:
        payload = self._launch_artifact_payload(attempt_number, plan)
        await self.store.write_artifact_json(run_id, node_id, "launch.json", payload)
        await self.store.write_artifact_json(run_id, node_id, f"launch-attempt-{attempt_number}.json", payload)

    async def _mark_node_cancelled(self, run_id: str, node_id: str, reason: str) -> None:
        record = self.store.get_run(run_id)
        result = record.nodes[node_id]
        result.status = NodeStatus.CANCELLED
        result.finished_at = utcnow_iso()
        if reason == "run_cancelled":
            await self.store.append_artifact_text(run_id, node_id, "stderr.log", "Cancelled by user\n")
        await self._publish(run_id, "node_cancelled", node_id=node_id, reason=reason)

    def _normalize_periodic_output_text(self, text: str | None) -> str:
        normalized = str(text or "").strip()
        if normalized.startswith("```"):
            lines = normalized.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                normalized = "\n".join(lines[1:-1]).strip()
                if normalized.lower().startswith("json\n"):
                    normalized = normalized[5:].strip()
        return normalized

    def _parse_periodic_actions(
        self,
        text: str | None,
    ) -> tuple[_PeriodicActionEnvelope | None, str | None]:
        normalized = self._normalize_periodic_output_text(text)
        if not normalized:
            return _PeriodicActionEnvelope(), None
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError as exc:
            return None, f"invalid JSON control envelope: {exc}"
        try:
            return _PeriodicActionEnvelope.model_validate(payload), None
        except ValidationError as exc:  # pragma: no cover - pydantic error details vary
            return None, f"invalid control envelope: {exc}"

    def _fanout_group_settled(self, pipeline: PipelineSpec, results: dict[str, NodeResult], group_id: str) -> bool:
        member_ids = pipeline.fanouts.get(group_id, [])
        if not member_ids:
            return True
        return all(results[member_id].status in _TERMINAL_NODE_STATUSES for member_id in member_ids)

    async def _finalize_periodic_node(self, run_id: str, node_id: str, *, reason: str) -> None:
        record = self.store.get_run(run_id)
        result = record.nodes[node_id]
        if result.status == NodeStatus.COMPLETED:
            return
        result.status = NodeStatus.COMPLETED
        result.success = True if result.success is None else result.success
        result.next_scheduled_at = None
        result.finished_at = result.finished_at or utcnow_iso()
        await self._publish(
            run_id,
            "node_completed",
            node_id=node_id,
            tick_count=result.tick_count,
            reason=reason,
            output=result.output,
            final_response=result.final_response,
            success=result.success,
            success_details=result.success_details,
        )
        await self.store.write_artifact_text(run_id, node_id, "output.txt", result.output or "")
        await self.store.write_artifact_json(run_id, node_id, "result.json", result.model_dump(mode="json"))
        await self.store.persist_run(run_id)

    async def _apply_periodic_actions(
        self,
        run_id: str,
        controller_node_id: str,
        *,
        watched_group: str,
        actions: _PeriodicActionEnvelope,
        remaining: set[str],
        in_progress: dict[str, asyncio.Task["_NodeExecutionOutcome"]],
    ) -> None:
        """Apply controller actions emitted by a periodic node to its watched fanout.

        Cancel actions mark running nodes for cooperative stop, while rerun actions
        either requeue finished nodes immediately or defer rerun until in-flight work
        reaches a terminal state.
        """

        if not actions.actions:
            return

        record = self.store.get_run(run_id)
        allowed_node_ids = set(record.pipeline.fanouts.get(watched_group, []))

        ordered_actions = sorted(actions.actions, key=lambda item: 0 if item.kind == "cancel" else 1)
        applied: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        for action in ordered_actions:
            kind = action.kind.strip().lower()
            if kind not in {"cancel", "rerun"}:
                rejected.append({"kind": action.kind, "node_ids": list(action.node_ids), "reason": "unsupported_action"})
                continue
            for target_node_id in action.node_ids:
                if target_node_id not in allowed_node_ids:
                    rejected.append({"kind": kind, "node_id": target_node_id, "reason": "outside_watched_fanout"})
                    continue
                target_result = record.nodes[target_node_id]
                if kind == "cancel":
                    if target_result.status not in {NodeStatus.QUEUED, NodeStatus.RUNNING, NodeStatus.RETRYING}:
                        rejected.append({"kind": kind, "node_id": target_node_id, "reason": "node_not_running"})
                        continue
                    self._node_cancel_flags.setdefault(run_id, set()).add(target_node_id)
                    applied.append({"kind": kind, "node_id": target_node_id, "reason": action.reason})
                    continue

                if target_result.status in {NodeStatus.PENDING, NodeStatus.READY}:
                    rejected.append({"kind": kind, "node_id": target_node_id, "reason": "node_not_started"})
                    continue
                self._pending_node_reruns.setdefault(run_id, set()).add(target_node_id)
                if target_result.status in _TERMINAL_NODE_STATUSES and target_node_id not in in_progress:
                    target_result.status = NodeStatus.PENDING
                    target_result.next_scheduled_at = None
                    remaining.add(target_node_id)
                applied.append({"kind": kind, "node_id": target_node_id, "reason": action.reason})

        if applied:
            await self._publish(
                run_id,
                "node_control_actions_applied",
                node_id=controller_node_id,
                watched_group=watched_group,
                actions=applied,
            )
        if rejected:
            await self._publish(
                run_id,
                "node_control_actions_rejected",
                node_id=controller_node_id,
                watched_group=watched_group,
                actions=rejected,
            )

    async def _execute_node(
        self,
        run_id: str,
        node_id: str,
        *,
        periodic_tick_number: int | None = None,
        periodic_tick_started_at: str | None = None,
    ) -> _NodeExecutionOutcome:
        """Execute one node from prompt preparation through final persisted result.

        The method renders the prompt, launches the adapter/runner pair, streams
        traces and artifacts, evaluates success, retries with backoff when needed,
        and honors run or node cancellation. Periodic ticks also parse optional
        control actions and return them to the scheduler.
        """

        record = self.store.get_run(run_id)
        pipeline = record.pipeline
        node = pipeline.node_map[node_id]
        result = record.nodes[node_id]
        result.started_at = result.started_at or (periodic_tick_started_at or utcnow_iso())
        if periodic_tick_number is not None:
            result.tick_count = max(result.tick_count, periodic_tick_number)
            result.last_tick_started_at = periodic_tick_started_at
        result.status = NodeStatus.RUNNING
        await self._publish(run_id, "node_started", node_id=node_id)
        if periodic_tick_number is not None:
            await self._publish(
                run_id,
                "node_tick_started",
                node_id=node_id,
                tick_number=periodic_tick_number,
                tick_started_at=periodic_tick_started_at,
            )

        prompt = render_node_prompt(
            pipeline,
            node,
            record.nodes,
            run_id=run_id,
            artifacts_base_dir=self.store.base_dir,
            current_tick_number=periodic_tick_number,
            current_tick_started_at=periodic_tick_started_at,
        )
        # Create git worktree if enabled (local nodes only get a worktree directory)
        worktree_dir = None
        if pipeline.use_worktree and node.target.kind == "local":
            from agentflow.worktree import create_worktree, is_git_repo
            if is_git_repo(pipeline.working_path):
                try:
                    worktree_dir = create_worktree(pipeline.working_path, node_id, run_id)
                    from types import SimpleNamespace
                    wt_target = SimpleNamespace(**{k: getattr(node.target, k) for k in node.target.model_fields})
                    wt_target.cwd = str(worktree_dir)
                    node = SimpleNamespace(
                        id=node.id, agent=node.agent, prompt=node.prompt,
                        target=wt_target, timeout_seconds=node.timeout_seconds,
                        retries=node.retries, retry_backoff_seconds=node.retry_backoff_seconds,
                        success_criteria=node.success_criteria, tools=node.tools,
                        model=node.model, capture=node.capture, env=node.env,
                        extra_args=node.extra_args, provider=node.provider,
                        mcps=node.mcps, skills=node.skills, schedule=node.schedule,
                        fanout_group=node.fanout_group, fanout_member=node.fanout_member,
                        on_failure_restart=node.on_failure_restart,
                        fanout_dependencies=getattr(node, 'fanout_dependencies', {}),
                    )
                except Exception as exc:
                    await self._publish(run_id, "node_trace", node_id=node_id,
                        trace={"kind": "warning", "title": f"Worktree failed: {exc}"})

        paths = self._build_paths(pipeline, run_id, node_id, node.target)

        # Inject scratchboard file location into prompt
        scratchboard = self._scratchboards.get(run_id)
        if scratchboard is not None:
            from agentflow.scratchboard import SCRATCHBOARD_FILENAME, SCRATCHBOARD_PROMPT_SUFFIX
            if node.target.kind == "local":
                sb_path = str(scratchboard.path)
            else:
                sb_path = f"{paths.target_runtime_dir}/{SCRATCHBOARD_FILENAME}"
            prompt += SCRATCHBOARD_PROMPT_SUFFIX.format(scratchboard_path=sb_path)
        adapter = self.adapters.get(node.agent)
        runner = self.runners.get(node.target.kind)
        parser = create_trace_parser(node.agent, node.id)
        periodic_actions: _PeriodicActionEnvelope | None = None
        periodic_action_parse_error: str | None = None

        for attempt_number in range(1, node.retries + 2):
            if self._should_cancel(run_id):
                await self._mark_node_cancelled(run_id, node_id, "run_cancelled")
                return _NodeExecutionOutcome(node_id=node_id, periodic_tick_number=periodic_tick_number)

            attempt = NodeAttempt(number=attempt_number, status=NodeStatus.RUNNING, started_at=utcnow_iso())
            attempt_stdout_lines: list[str] = []
            attempt_stderr_lines: list[str] = []
            result.current_attempt = attempt_number
            result.attempts.append(attempt)
            parser.start_attempt(attempt_number)
            prepared = adapter.prepare(node, prompt, paths)
            # Forward local credentials to remote targets when enabled
            # EC2/ECS: always forward (ephemeral, no pre-existing config)
            # SSH: only if forward_credentials=True (remote has its own identity)
            should_forward = (
                node.target.kind in ("ec2", "ecs")
                or (node.target.kind == "ssh" and getattr(node.target, "forward_credentials", False))
            )
            if should_forward:
                from agentflow.cloud.aws import collect_local_credentials
                local_creds = collect_local_credentials(node.agent.value)
                merged = {**local_creds, **prepared.env}
                prepared = PreparedExecution(
                    command=prepared.command, env=merged, cwd=prepared.cwd,
                    trace_kind=prepared.trace_kind, runtime_files=prepared.runtime_files,
                    stdin=prepared.stdin,
                )
            # Inject scratchboard file into runtime_files for remote targets
            if scratchboard is not None and node.target.kind not in ("local",):
                from agentflow.scratchboard import SCRATCHBOARD_FILENAME
                prepared.runtime_files[SCRATCHBOARD_FILENAME] = scratchboard.read()
            plan = runner.plan_execution(node, prepared, paths)
            await self._write_launch_artifacts(run_id, node_id, attempt_number, plan)
            await self.store.append_artifact_text(
                run_id,
                node_id,
                "stdout.log",
                f"\n=== attempt {attempt_number} started {attempt.started_at} ===\n",
            )
            await self.store.append_artifact_text(
                run_id,
                node_id,
                "stderr.log",
                f"\n=== attempt {attempt_number} started {attempt.started_at} ===\n",
            )
            if attempt_number > 1:
                result.status = NodeStatus.RETRYING
                await self._publish(
                    run_id,
                    "node_retrying",
                    node_id=node_id,
                    attempt=attempt_number,
                    max_attempts=node.retries + 1,
                )
                result.status = NodeStatus.RUNNING

            async def on_output(stream_name: str, line: str) -> None:
                if stream_name == "stdout":
                    await self.store.append_artifact_text(run_id, node_id, "stdout.log", line + "\n")
                    parsed_events = parser.feed(line)
                    if parsed_events or parser.supports_raw_stdout_fallback():
                        attempt_stdout_lines.append(line)
                    for event in parsed_events:
                        result.trace_events.append(event)
                        await self._publish_trace(run_id, node_id, event)
                else:
                    attempt_stderr_lines.append(line)
                    await self.store.append_artifact_text(run_id, node_id, "stderr.log", line + "\n")
                    event = parser.emit("stderr", "stderr", line, line, source="stderr")
                    result.trace_events.append(event)
                    await self._publish_trace(run_id, node_id, event)

            raw = await runner.execute(
                node,
                prepared,
                paths,
                on_output,
                lambda: self._should_cancel(run_id) or self._should_cancel_node(run_id, node_id),
            )
            result.exit_code = raw.exit_code
            result.stdout_lines = attempt_stdout_lines
            result.stderr_lines = attempt_stderr_lines
            result.final_response = parser.finalize()
            if not result.final_response and parser.supports_raw_stdout_fallback():
                result.final_response = "\n".join(attempt_stdout_lines).strip()
            result.output = result.final_response if node.capture.value == "final" else "\n".join(attempt_stdout_lines)
            success_ok, success_details = evaluate_success(node, result, paths.host_workdir)
            result.success = success_ok
            result.success_details = success_details
            attempt.finished_at = utcnow_iso()
            attempt.exit_code = raw.exit_code
            attempt.final_response = result.final_response
            attempt.output = result.output
            attempt.success = success_ok
            attempt.success_details = success_details

            if raw.cancelled or self._should_cancel(run_id):
                attempt.status = NodeStatus.CANCELLED
                result.status = NodeStatus.CANCELLED
                result.finished_at = attempt.finished_at
                await self._publish(
                    run_id,
                    "node_cancelled",
                    node_id=node_id,
                    attempt=attempt_number,
                    exit_code=raw.exit_code,
                )
                break

            if raw.exit_code == 0 and success_ok:
                attempt.status = NodeStatus.COMPLETED
                result.status = NodeStatus.READY if periodic_tick_number is not None else NodeStatus.COMPLETED
                result.finished_at = attempt.finished_at
                if periodic_tick_number is not None:
                    if node.schedule and node.schedule.actuation == PeriodicActuationMode.OUTPUT_JSON:
                        periodic_actions, periodic_action_parse_error = self._parse_periodic_actions(result.final_response)
                        if periodic_actions is not None and periodic_actions.analysis is not None:
                            result.output = periodic_actions.analysis
                            attempt.output = result.output
                    await self._publish(
                        run_id,
                        "node_tick_completed",
                        node_id=node_id,
                        tick_number=periodic_tick_number,
                        attempt=attempt_number,
                        exit_code=result.exit_code,
                        success=result.success,
                        output=result.output,
                        final_response=result.final_response,
                        success_details=result.success_details,
                    )
                else:
                    await self._publish(
                        run_id,
                        "node_completed",
                        node_id=node_id,
                        attempt=attempt_number,
                        exit_code=result.exit_code,
                        success=result.success,
                        output=result.output,
                        final_response=result.final_response,
                        success_details=result.success_details,
                    )
                break

            attempt.status = NodeStatus.FAILED
            result.status = NodeStatus.FAILED
            result.finished_at = attempt.finished_at
            await self._publish(
                run_id,
                "node_failed",
                node_id=node_id,
                attempt=attempt_number,
                exit_code=result.exit_code,
                success=result.success,
                output=result.output,
                final_response=result.final_response,
                success_details=result.success_details,
            )
            if attempt_number <= node.retries:
                await asyncio.sleep(max(node.retry_backoff_seconds, 0.0) * attempt_number)
                continue
            break

        await self.store.write_artifact_text(run_id, node_id, "output.txt", result.output or "")
        await self.store.write_artifact_json(run_id, node_id, "result.json", result.model_dump(mode="json"))

        # Merge scratchboard writes from node output
        scratchboard = self._scratchboards.get(run_id)
        if scratchboard is not None and result.output:
            for line in result.output.splitlines():
                stripped = line.strip()
                if stripped.startswith("SCRATCHBOARD:"):
                    content = stripped.removeprefix("SCRATCHBOARD:").strip()
                    await scratchboard.append(node_id, content)

        # Capture diff from worktree (local) or remote (SSH/EC2/ECS)
        if pipeline.use_worktree:
            diff = ""
            if worktree_dir is not None:
                # Local: diff from worktree
                from agentflow.worktree import get_worktree_diff, remove_worktree
                try:
                    diff = get_worktree_diff(worktree_dir)
                except Exception:
                    pass
            # For remote nodes (SSH/EC2/ECS), diff is captured from the node output
            # if the node prompt asks for `git diff`. No automatic remote diff capture.
            if diff:
                await self.store.write_artifact_text(run_id, node_id, "diff.patch", diff)
            result.diff = diff

            # Clean up worktree
            if worktree_dir is not None:
                from agentflow.worktree import remove_worktree
                try:
                    remove_worktree(pipeline.working_path, worktree_dir)
                except Exception:
                    pass

        await self.store.persist_run(run_id)
        if periodic_tick_number is not None:
            return _NodeExecutionOutcome(
                node_id=node_id,
                periodic_tick_number=periodic_tick_number,
                periodic_actions=periodic_actions,
                periodic_action_parse_error=periodic_action_parse_error,
            )
        return _NodeExecutionOutcome(node_id=node_id)

    async def run(self, run_id: str) -> RunRecord:
        """Drive a run until all nodes reach terminal outcomes.

        The loop skips nodes blocked by upstream failure, queues nodes whose
        dependencies are satisfied, and bounds concurrent execution with a
        semaphore. `_execute_node()` handles per-node retry attempts; this loop
        handles scheduling, completion collection, and explicit reruns. Periodic
        nodes execute as repeated ticks, can emit cancel/rerun actions for a watched
        fanout, reschedule on `every_seconds`, and finalize once that fanout group
        has fully settled.
        """

        record = self.store.get_run(run_id)
        pipeline = record.pipeline
        record.status = RunStatus.RUNNING
        record.started_at = utcnow_iso()
        await self._publish(run_id, "run_started", pipeline=pipeline.model_dump(mode="json"))
        await self.store.persist_run(run_id)

        node_map = pipeline.node_map
        iteration_counts: dict[tuple[str, str], int] = {}

        # Create scratchboard if enabled
        if pipeline.scratchboard:
            from agentflow.scratchboard import Scratchboard, SCRATCHBOARD_FILENAME
            sb_path = self.store.base_dir / run_id / SCRATCHBOARD_FILENAME
            self._scratchboards[run_id] = Scratchboard(sb_path)

        # Pre-register shared resource counts so instances survive between sequential nodes
        self._register_shared_resources(pipeline)
        remaining = set(node_map)
        in_progress: dict[str, asyncio.Task[_NodeExecutionOutcome]] = {}
        semaphore = asyncio.Semaphore(pipeline.concurrency)
        loop = asyncio.get_running_loop()
        periodic_state = {
            node_id: _PeriodicNodeRuntimeState()
            for node_id, node in node_map.items()
            if node.schedule is not None
        }

        async def launch(node_id: str) -> _NodeExecutionOutcome:
            async with semaphore:
                node = node_map[node_id]
                if node.schedule is None:
                    return await self._execute_node(run_id, node_id)
                state = periodic_state[node_id]
                state.tick_count += 1
                tick_started_at = utcnow_iso()
                state.last_tick_started_at = tick_started_at
                state.last_tick_started_mono = loop.time()
                record.nodes[node_id].tick_count = state.tick_count
                record.nodes[node_id].last_tick_started_at = tick_started_at
                record.nodes[node_id].next_scheduled_at = None
                return await self._execute_node(
                    run_id,
                    node_id,
                    periodic_tick_number=state.tick_count,
                    periodic_tick_started_at=tick_started_at,
                )

        while remaining or in_progress:
            if self._should_cancel(run_id):
                for node_id in list(remaining):
                    await self._mark_node_cancelled(run_id, node_id, "run_cancelled")
                    remaining.remove(node_id)
                if not in_progress:
                    break

            failed_nodes = {node_id for node_id, node in record.nodes.items() if node.status == NodeStatus.FAILED}
            if pipeline.fail_fast and failed_nodes:
                for node_id in list(remaining):
                    record.nodes[node_id].status = NodeStatus.SKIPPED
                    record.nodes[node_id].finished_at = utcnow_iso()
                    remaining.remove(node_id)
                    await self._publish(run_id, "node_skipped", node_id=node_id, reason="fail_fast")

            # Collect nodes involved in cycles (both targets and tail nodes)
            cycle_nodes: set[str] = set()
            for n in pipeline.nodes:
                if n.on_failure_restart:
                    cycle_nodes.add(n.id)  # tail node (has the back-edge)
                    cycle_nodes.update(n.on_failure_restart)  # restart targets

            blocked = [
                node_id
                for node_id in list(remaining)
                if node_id not in cycle_nodes  # don't skip any node in a cycle
                and any(record.nodes[dependency].status in {NodeStatus.FAILED, NodeStatus.SKIPPED, NodeStatus.CANCELLED} for dependency in node_map[node_id].depends_on)
            ]
            for node_id in blocked:
                record.nodes[node_id].status = NodeStatus.SKIPPED
                record.nodes[node_id].finished_at = utcnow_iso()
                remaining.remove(node_id)
                await self._publish(run_id, "node_skipped", node_id=node_id, reason="upstream_failure")
            for node_id in list(remaining):
                node = node_map[node_id]
                if node.schedule is None:
                    continue
                if any(record.nodes[dependency].status != NodeStatus.COMPLETED for dependency in node.depends_on):
                    continue
                if not self._fanout_group_settled(
                    pipeline,
                    record.nodes,
                    node.schedule.until_fanout_settles_from,
                ):
                    continue
                remaining.remove(node_id)
                await self._finalize_periodic_node(run_id, node_id, reason="watched_group_settled")

            now = loop.time()
            ready: list[str] = []
            for node_id in list(remaining):
                if node_id in in_progress:
                    continue
                node = node_map[node_id]
                # Cycle nodes can proceed when deps are COMPLETED or FAILED
                if node_id in cycle_nodes or node.on_failure_restart:
                    terminal = {NodeStatus.COMPLETED, NodeStatus.FAILED}
                    if not all(record.nodes[dep].status in terminal for dep in node.depends_on):
                        continue
                elif not all(record.nodes[dep].status == NodeStatus.COMPLETED for dep in node.depends_on):
                    continue
                if node.schedule is None:
                    ready.append(node_id)
                    continue
                state = periodic_state[node_id]
                if state.next_tick_at is None or now >= state.next_tick_at:
                    ready.append(node_id)
            for node_id in ready:
                if node_id not in in_progress:
                    remaining.remove(node_id)
                    record.nodes[node_id].status = NodeStatus.QUEUED
                    in_progress[node_id] = asyncio.create_task(launch(node_id))
            if in_progress:
                done, _ = await asyncio.wait(in_progress.values(), timeout=0.1, return_when=asyncio.FIRST_COMPLETED)
                finished_ids = [node_id for node_id, task in in_progress.items() if task in done]
                for node_id in finished_ids:
                    task = in_progress.pop(node_id)
                    outcome = await task
                    node = node_map[node_id]
                    self._node_cancel_flags.setdefault(run_id, set()).discard(node_id)

                    if node.schedule is not None:
                        if outcome.periodic_actions is not None:
                            await self.store.write_artifact_json(
                                run_id,
                                node_id,
                                f"periodic-actions-tick-{outcome.periodic_tick_number}.json",
                                outcome.periodic_actions.model_dump(mode="json"),
                            )
                        elif outcome.periodic_action_parse_error is not None:
                            await self.store.write_artifact_json(
                                run_id,
                                node_id,
                                f"periodic-actions-tick-{outcome.periodic_tick_number}.json",
                                {"error": outcome.periodic_action_parse_error},
                            )
                            await self._publish(
                                run_id,
                                "node_control_actions_rejected",
                                node_id=node_id,
                                watched_group=node.schedule.until_fanout_settles_from,
                                actions=[{"reason": outcome.periodic_action_parse_error}],
                            )

                        if outcome.periodic_actions is not None:
                            await self._apply_periodic_actions(
                                run_id,
                                node_id,
                                watched_group=node.schedule.until_fanout_settles_from,
                                actions=outcome.periodic_actions,
                                remaining=remaining,
                                in_progress=in_progress,
                            )

                        node_result = record.nodes[node_id]
                        if node_result.status == NodeStatus.READY and not self._should_cancel(run_id):
                            if self._fanout_group_settled(
                                pipeline,
                                record.nodes,
                                node.schedule.until_fanout_settles_from,
                            ):
                                await self._finalize_periodic_node(run_id, node_id, reason="watched_group_settled")
                            else:
                                state = periodic_state[node_id]
                                if state.last_tick_started_mono is None:
                                    state.next_tick_at = loop.time() + node.schedule.every_seconds
                                else:
                                    state.next_tick_at = state.last_tick_started_mono + node.schedule.every_seconds
                                seconds_until_next_tick = max(state.next_tick_at - loop.time(), 0.0)
                                next_tick_at = datetime.now(timezone.utc) + timedelta(seconds=seconds_until_next_tick)
                                node_result.next_scheduled_at = next_tick_at.isoformat()
                                remaining.add(node_id)
                                await self._publish(
                                    run_id,
                                    "node_waiting",
                                    node_id=node_id,
                                    tick_count=node_result.tick_count,
                                    next_scheduled_at=node_result.next_scheduled_at,
                                )
                                await self.store.persist_run(run_id)

                    # -- on_failure_restart: cycle back-edge handling --
                    if (
                        record.nodes[node_id].status == NodeStatus.FAILED
                        and node.on_failure_restart
                        and not self._should_cancel(run_id)
                    ):
                        iteration_key = (run_id, node_id)
                        iteration_counts[iteration_key] = iteration_counts.get(iteration_key, 0) + 1
                        if iteration_counts[iteration_key] < pipeline.max_iterations:
                            await self._publish(
                                run_id, "node_cycle_restart",
                                node_id=node_id,
                                iteration=iteration_counts[iteration_key],
                                restart_targets=node.on_failure_restart,
                            )
                            # Reset the failed node itself
                            record.nodes[node_id].status = NodeStatus.PENDING
                            record.nodes[node_id].finished_at = None
                            remaining.add(node_id)
                            # Reset all restart targets and their downstream chain
                            for target_id in node.on_failure_restart:
                                self._reset_node_for_cycle(record, target_id, remaining)
                                # Also reset nodes between target and this node
                                for mid_id in self._nodes_between(node_map, target_id, node_id):
                                    self._reset_node_for_cycle(record, mid_id, remaining)
                            await self.store.persist_run(run_id)
                        else:
                            await self._publish(
                                run_id, "node_cycle_exhausted",
                                node_id=node_id,
                                max_iterations=pipeline.max_iterations,
                            )

                    if (
                        node_id in self._pending_node_reruns.setdefault(run_id, set())
                        and record.nodes[node_id].status in _TERMINAL_NODE_STATUSES
                        and not self._should_cancel(run_id)
                    ):
                        self._pending_node_reruns[run_id].discard(node_id)
                        record.nodes[node_id].status = NodeStatus.PENDING
                        record.nodes[node_id].finished_at = None
                        record.nodes[node_id].next_scheduled_at = None
                        remaining.add(node_id)
                        await self._publish(run_id, "node_rerun_queued", node_id=node_id)
                        await self.store.persist_run(run_id)
            elif remaining:
                await asyncio.sleep(0.05)
            else:
                break

        if record.status == RunStatus.CANCELLING or self._should_cancel(run_id):
            record.status = RunStatus.CANCELLED
        elif any(node.status == NodeStatus.FAILED for node in record.nodes.values()):
            record.status = RunStatus.FAILED
        else:
            record.status = RunStatus.COMPLETED
        record.finished_at = utcnow_iso()
        await self._publish(run_id, "run_completed", status=record.status.value)
        await self.store.clear_cancel_request(run_id)
        await self.store.persist_run(run_id)
        self._node_cancel_flags.pop(run_id, None)
        self._pending_node_reruns.pop(run_id, None)
        return record
