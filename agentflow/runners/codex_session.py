from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_THREAD_ID_PATTERN = re.compile(r"^[0-9a-fA-F-]{36}$")


@dataclass(frozen=True, slots=True)
class CodexSessionCompletion:
    exit_code: int
    final_message: str | None = None
    terminal_event_seen_on_stdout: bool = False
    final_agent_message_seen_on_stdout: bool = False


class CodexSessionCompletionMonitor:
    """Recover when a completed local Codex turn stalls during CLI shutdown.

    ``codex exec --json`` normally emits ``turn.completed`` and exits. Codex also
    persists the same turn lifecycle to its rollout JSONL. In rare shutdown
    failures the persisted root turn reaches ``task_complete`` while the exec
    event stream and process remain stuck. This monitor treats that matching
    persisted event as an authoritative fallback after a grace period.
    """

    _POLL_SECONDS = 0.25

    def __init__(self, codex_home: Path, *, stall_grace_seconds: float) -> None:
        self.codex_home = codex_home
        self.stall_grace_seconds = stall_grace_seconds
        self.thread_id: str | None = None
        self.terminal_event_seen_on_stdout = False
        self._agent_messages_seen_on_stdout: set[str] = set()
        self._thread_started = asyncio.Event()
        self._initial_sessions = self._snapshot_sessions()

    def _snapshot_sessions(self) -> dict[Path, tuple[int, int, int]]:
        snapshots: dict[Path, tuple[int, int, int]] = {}
        sessions_root = self.codex_home / "sessions"
        try:
            paths = list(sessions_root.rglob("*.jsonl")) if sessions_root.is_dir() else []
        except OSError:
            return snapshots
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            if path.is_file():
                snapshots[path] = (stat.st_dev, stat.st_ino, stat.st_size)
        return snapshots

    @classmethod
    def for_execution(
        cls,
        *,
        trace_kind: str,
        target_kind: str,
        command: list[str],
        env: dict[str, str],
        stall_grace_seconds: float,
    ) -> CodexSessionCompletionMonitor | None:
        codex_home = env.get("CODEX_HOME")
        if (
            trace_kind != "codex"
            or target_kind != "local"
            or not codex_home
            or "--ephemeral" in command
        ):
            return None
        return cls(Path(codex_home), stall_grace_seconds=stall_grace_seconds)

    def observe_stdout(self, line: str) -> None:
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        event_type = payload.get("type")
        if event_type == "thread.started":
            thread_id = payload.get("thread_id")
            if isinstance(thread_id, str) and _THREAD_ID_PATTERN.fullmatch(thread_id):
                self.thread_id = thread_id
                self._thread_started.set()
        elif event_type in {"turn.completed", "turn.failed"}:
            self.terminal_event_seen_on_stdout = True
        elif event_type in {"item.completed", "item/completed"}:
            item = payload.get("item")
            if not isinstance(item, dict):
                params = payload.get("params")
                item = params.get("item") if isinstance(params, dict) else None
            if isinstance(item, dict) and item.get("type") in {
                "agent_message",
                "agentMessage",
            }:
                text = item.get("text")
                if isinstance(text, str):
                    self._agent_messages_seen_on_stdout.add(text)

    def with_current_stdout_observations(
        self,
        completion: CodexSessionCompletion,
    ) -> CodexSessionCompletion:
        final_message = completion.final_message
        return CodexSessionCompletion(
            exit_code=completion.exit_code,
            final_message=final_message,
            terminal_event_seen_on_stdout=self.terminal_event_seen_on_stdout,
            final_agent_message_seen_on_stdout=(
                final_message is not None
                and final_message in self._agent_messages_seen_on_stdout
            ),
        )

    async def wait(self) -> CodexSessionCompletion:
        await self._thread_started.wait()
        assert self.thread_id is not None
        session_path = await self._find_session_path(self.thread_id)
        return await self._wait_for_terminal_event(
            session_path,
            self._invocation_offset(session_path),
        )

    def _invocation_offset(self, path: Path) -> int:
        baseline = self._initial_sessions.get(path)
        if baseline is None:
            return 0
        try:
            stat = path.stat()
        except OSError:
            return 0
        if (stat.st_dev, stat.st_ino) != baseline[:2]:
            return 0
        return min(baseline[2], stat.st_size)

    async def _find_session_path(self, thread_id: str) -> Path:
        sessions_root = self.codex_home / "sessions"
        pattern = f"*{thread_id}.jsonl"
        while True:
            if sessions_root.is_dir():
                matches = [path for path in sessions_root.rglob(pattern) if path.is_file()]
                if matches:
                    return max(matches, key=lambda path: path.stat().st_mtime_ns)
            await asyncio.sleep(self._POLL_SECONDS)

    def _parse_lifecycle_event(
        self,
        line: str,
    ) -> tuple[str | None, str | None, str | None]:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            return None, None, None
        if not isinstance(record, dict) or record.get("type") != "event_msg":
            return None, None, None
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return None, None, None
        event_type = payload.get("type")
        turn_id = payload.get("turn_id")
        if not isinstance(turn_id, str):
            return None, None, None
        if event_type == "task_started":
            return "started", turn_id, None
        if event_type == "task_complete":
            final_message = payload.get("last_agent_message")
            return "complete", turn_id, final_message if isinstance(final_message, str) else None
        if event_type == "turn_aborted":
            return "aborted", turn_id, None
        return None, None, None

    async def _wait_for_terminal_event(
        self,
        path: Path,
        offset: int,
    ) -> CodexSessionCompletion:
        active_turn_id: str | None = None
        fragment = ""
        candidate: CodexSessionCompletion | None = None
        candidate_deadline: float | None = None
        loop = asyncio.get_running_loop()
        while True:
            try:
                with path.open("r", encoding="utf-8", errors="replace") as stream:
                    stream.seek(offset)
                    chunk = stream.read()
                    offset = stream.tell()
            except OSError:
                await asyncio.sleep(self._POLL_SECONDS)
                continue

            if chunk:
                complete_lines = (fragment + chunk).split("\n")
                fragment = complete_lines.pop()
                for line in complete_lines:
                    lifecycle, turn_id, final_message = self._parse_lifecycle_event(line)
                    if lifecycle == "started":
                        active_turn_id = turn_id
                        candidate = None
                        candidate_deadline = None
                    elif lifecycle in {"complete", "aborted"} and turn_id == active_turn_id:
                        candidate = CodexSessionCompletion(
                            exit_code=0 if lifecycle == "complete" else 1,
                            final_message=final_message,
                            terminal_event_seen_on_stdout=self.terminal_event_seen_on_stdout,
                        )
                        candidate_deadline = loop.time() + self.stall_grace_seconds
            if (
                candidate is not None
                and candidate_deadline is not None
                and loop.time() >= candidate_deadline
            ):
                return self.with_current_stdout_observations(candidate)
            sleep_for = self._POLL_SECONDS
            if candidate_deadline is not None:
                sleep_for = min(sleep_for, max(0, candidate_deadline - loop.time()))
            await asyncio.sleep(sleep_for)

    @staticmethod
    def recovered_stdout_events(completion: CodexSessionCompletion) -> list[str]:
        if completion.terminal_event_seen_on_stdout:
            return []
        events: list[dict[str, Any]] = []
        if (
            completion.exit_code == 0
            and completion.final_message
            and not completion.final_agent_message_seen_on_stdout
        ):
            events.append(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "agentflow_recovered_final",
                        "type": "agent_message",
                        "text": completion.final_message,
                    },
                    "recovered_from": "codex_session",
                }
            )
        events.append(
            {
                "type": "turn.completed" if completion.exit_code == 0 else "turn.failed",
                "recovered_from": "codex_session",
            }
        )
        return [json.dumps(event, ensure_ascii=False) for event in events]
