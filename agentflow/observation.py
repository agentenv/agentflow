from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from agentflow.specs import NodeResult, NodeStatus, NormalizedTraceEvent, RunEvent, RunRecord


_ACTIVE_NODE_STATUSES = {
    NodeStatus.PENDING.value,
    NodeStatus.QUEUED.value,
    NodeStatus.READY.value,
    NodeStatus.RUNNING.value,
    NodeStatus.RETRYING.value,
}
_PYTEST_SUMMARY_PATTERN = re.compile(
    r"(?P<passed>\d+)\s+passed"
    r"(?:,\s+(?P<failed>\d+)\s+failed)?"
    r"(?:,\s+(?P<errors>\d+)\s+error[s]?)?",
)
_READ_ONLY_COMMAND_PREFIXES = ("sed ", "cat ", "rg ", "ls ", "find ")


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim_message(value: str | None, *, limit: int = 140) -> str | None:
    if value is None:
        return None
    collapsed = " ".join(str(value).split())
    if not collapsed:
        return None
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _raw_trace(event: RunEvent) -> NormalizedTraceEvent | None:
    if event.type != "node_trace" or not isinstance(event.data, dict):
        return None
    trace = event.data.get("trace")
    if not isinstance(trace, dict):
        return None
    try:
        return NormalizedTraceEvent.model_validate(trace)
    except Exception:
        return None


def _command_from_trace(trace: NormalizedTraceEvent) -> str | None:
    raw = trace.raw
    if not isinstance(raw, dict):
        return None
    item = raw.get("item")
    if not isinstance(item, dict):
        return None
    command = item.get("command")
    return str(command) if command else None


def _command_result_from_trace(trace: NormalizedTraceEvent) -> tuple[int | None, str | None]:
    raw = trace.raw
    if not isinstance(raw, dict):
        return None, None
    item = raw.get("item")
    if not isinstance(item, dict):
        return None, None
    exit_code = item.get("exit_code")
    output = item.get("aggregated_output")
    return int(exit_code) if isinstance(exit_code, int) else None, str(output) if output is not None else None


def summarize_command_result(command: str, exit_code: int | None, aggregated_output: str | None) -> str:
    short_command = _trim_message(command, limit=120) or "command"
    output_text = str(aggregated_output or "")
    if "pytest" in command:
        lines = [line.strip() for line in output_text.splitlines() if line.strip()]
        summary_line = next((line for line in reversed(lines) if any(token in line for token in ("passed", "failed", "error"))), None)
        if summary_line:
            matched = _PYTEST_SUMMARY_PATTERN.search(summary_line)
            if matched:
                parts = [f"{matched.group('passed')} passed"]
                if matched.group("failed"):
                    parts.append(f"{matched.group('failed')} failed")
                if matched.group("errors"):
                    parts.append(f"{matched.group('errors')} errors")
                return f"{short_command} -> {', '.join(parts)}"
        if exit_code is not None:
            return f"{short_command} -> exit {exit_code}"
    if command.startswith(_READ_ONLY_COMMAND_PREFIXES):
        return f"command completed: {short_command}"
    if exit_code not in (None, 0):
        lines = [line.strip() for line in output_text.splitlines() if line.strip()]
        tail = _trim_message(lines[-1], limit=100) if lines else None
        if tail:
            return f"{short_command} -> exit {exit_code}: {tail}"
        return f"{short_command} -> exit {exit_code}"
    return f"command completed: {short_command}"


def summarize_progress_event(event: RunEvent) -> tuple[str | None, str | None]:
    trace = _raw_trace(event)
    if trace is None:
        if event.type in {"node_started", "node_retrying", "node_completed", "node_failed", "node_cancelled"}:
            kind = event.type
            if event.type == "node_retrying":
                attempt = event.data.get("attempt") if isinstance(event.data, dict) else None
                message = f"retry attempt {attempt}" if attempt else "retrying"
            elif event.type == "node_started":
                message = "node started"
            elif event.type == "node_completed":
                message = _trim_message((event.data or {}).get("output") or (event.data or {}).get("final_response")) or "completed"
            elif event.type == "node_failed":
                message = _trim_message((event.data or {}).get("output") or (event.data or {}).get("final_response")) or "failed"
            else:
                message = "cancelled"
            return kind, message
        return None, None

    title = trace.title.lower()
    if trace.kind == "assistant_message":
        return "assistant_message", _trim_message(trace.content)
    if trace.kind == "item_completed" and "agent_message" in title:
        return "agent_message", _trim_message(trace.content)
    if trace.kind == "item_started" and "command_execution" in title:
        command = _command_from_trace(trace)
        return "command_started", _trim_message(command)
    if trace.kind == "item_completed" and "command_execution" in title:
        command = _command_from_trace(trace) or "command"
        exit_code, aggregated_output = _command_result_from_trace(trace)
        return "command_completed", summarize_command_result(command, exit_code, aggregated_output)
    if trace.kind == "tool_call":
        return "tool_call", _trim_message(trace.title)
    if trace.kind == "stderr":
        return "stderr", _trim_message(trace.content)
    return None, None


def _is_active_node(node: NodeResult) -> bool:
    return node.status.value in _ACTIVE_NODE_STATUSES


def _update_node_staleness(
    node: NodeResult,
    *,
    now: datetime,
    quiet_after_seconds: int,
    stale_after_seconds: int,
) -> None:
    if not _is_active_node(node):
        node.stale_since = None
        return
    last_progress = _parse_iso8601(node.last_progress_at) or _parse_iso8601(node.last_trace_at) or _parse_iso8601(node.last_event_at)
    if last_progress is None:
        node.stale_since = now.isoformat()
        return
    age = (now - last_progress).total_seconds()
    if age >= stale_after_seconds:
        if node.stale_since is None:
            node.stale_since = now.isoformat()
    elif age < quiet_after_seconds:
        node.stale_since = None


def _refresh_run_aggregates(record: RunRecord, *, now: datetime, quiet_after_seconds: int, stale_after_seconds: int) -> None:
    active: list[str] = []
    stale: list[str] = []
    for node_id, node in record.nodes.items():
        _update_node_staleness(node, now=now, quiet_after_seconds=quiet_after_seconds, stale_after_seconds=stale_after_seconds)
        if _is_active_node(node):
            active.append(node_id)
        if node.stale_since is not None:
            stale.append(node_id)
    record.active_node_ids = sorted(active)
    record.stale_node_ids = sorted(stale)


def update_observation_state(
    record: RunRecord,
    event: RunEvent,
    *,
    now: str | None = None,
    quiet_after_seconds: int = 60,
    stale_after_seconds: int = 300,
) -> None:
    now_iso = now or event.timestamp or _utcnow_iso()
    now_dt = _parse_iso8601(now_iso) or datetime.now(timezone.utc)
    record.last_event_at = now_iso

    node = record.nodes.get(event.node_id) if event.node_id else None
    if node is not None:
        node.last_event_at = now_iso
        trace = _raw_trace(event)
        if trace is not None:
            node.last_trace_at = trace.timestamp or now_iso
            trace_kind, trace_message = summarize_progress_event(event)
            command = _command_from_trace(trace)
            if trace_kind == "command_started" and command:
                node.last_command = command
                node.last_command_status = "running"
                node.last_command_started_at = now_iso
            elif trace_kind == "command_completed":
                if command:
                    node.last_command = command
                node.last_command_status = "completed"
                node.last_command_finished_at = now_iso
            if trace_kind and trace_message:
                if trace_message != node.last_progress_message or trace_kind != node.last_progress_kind:
                    node.progress_count += 1
                node.last_progress_kind = trace_kind
                node.last_progress_message = trace_message
                node.last_progress_at = now_iso
                node.stale_since = None
                record.last_progress_at = now_iso
        else:
            progress_kind, progress_message = summarize_progress_event(event)
            if progress_kind and progress_message:
                if progress_message != node.last_progress_message or progress_kind != node.last_progress_kind:
                    node.progress_count += 1
                node.last_progress_kind = progress_kind
                node.last_progress_message = progress_message
                node.last_progress_at = now_iso
                node.stale_since = None
                record.last_progress_at = now_iso
            if event.type == "node_started":
                node.last_progress_at = now_iso
                node.stale_since = None

    _refresh_run_aggregates(
        record,
        now=now_dt,
        quiet_after_seconds=quiet_after_seconds,
        stale_after_seconds=stale_after_seconds,
    )
