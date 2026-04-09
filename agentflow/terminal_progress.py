from __future__ import annotations

from datetime import datetime


class TerminalProgressRenderer:
    def __init__(self) -> None:
        self._last_by_node: dict[str, tuple[str, str]] = {}

    def render_progress(
        self,
        *,
        node_id: str,
        kind: str,
        message: str | None,
        timestamp: str | None,
    ) -> list[str]:
        if not message:
            return []
        signature = (kind, message)
        if self._last_by_node.get(node_id) == signature:
            return []
        self._last_by_node[node_id] = signature
        rendered_time = self._render_time(timestamp)
        return [f"[{rendered_time}] {node_id}: {message}"]

    def render_snapshot(self, *, run_id: str, status: str, progress_summary: str) -> list[str]:
        return [f"Run {run_id}: {status}", progress_summary]

    def _render_time(self, timestamp: str | None) -> str:
        if not timestamp:
            return "--:--:--"
        normalized = timestamp.replace("Z", "+00:00")
        try:
            value = datetime.fromisoformat(normalized)
        except ValueError:
            return "--:--:--"
        return value.strftime("%H:%M:%S")
