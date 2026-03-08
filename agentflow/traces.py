from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agentflow.specs import AgentKind, NormalizedTraceEvent


def _json(line: str) -> Any | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := _stringify(item)))
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("text", "delta", "content", "output", "result", "message", "arguments_part"):
            if key in value:
                text = _stringify(value[key])
                if text:
                    parts.append(text)
        if parts:
            return "\n".join(parts)
    return ""


@dataclass(slots=True)
class BaseTraceParser:
    node_id: str
    agent: AgentKind
    attempt: int = 1
    final_chunks: list[str] = field(default_factory=list)
    last_message: str | None = None

    def emit(self, kind: str, title: str, content: str | None = None, raw: Any | None = None, source: str = "stdout") -> NormalizedTraceEvent:
        return NormalizedTraceEvent(
            node_id=self.node_id,
            agent=self.agent,
            attempt=self.attempt,
            source=source,
            kind=kind,
            title=title,
            content=content,
            raw=raw,
        )

    def start_attempt(self, attempt: int) -> None:
        self.attempt = attempt
        self.final_chunks.clear()
        self.last_message = None

    def remember(self, text: str | None) -> None:
        if text:
            self.final_chunks.append(text)
            self.last_message = text

    def feed(self, line: str) -> list[NormalizedTraceEvent]:
        raise NotImplementedError

    def finalize(self) -> str:
        joined = "\n".join(chunk.strip() for chunk in self.final_chunks if chunk and chunk.strip()).strip()
        return joined or (self.last_message or "")


@dataclass(slots=True)
class CodexTraceParser(BaseTraceParser):
    def feed(self, line: str) -> list[NormalizedTraceEvent]:
        payload = _json(line)
        if payload is None:
            text = line.rstrip()
            self.remember(text)
            return [self.emit("stdout", "stdout", text, line)] if text else []

        event_type = payload.get("type") or payload.get("method") or payload.get("event") or "codex"
        events: list[NormalizedTraceEvent] = []

        if event_type in {"response.output_text.delta", "agent_message_delta", "item/agentMessage/delta"}:
            text = _stringify(payload.get("delta") or payload.get("params") or payload)
            self.remember(text)
            events.append(self.emit("assistant_delta", "Assistant delta", text, payload))
        elif event_type == "response.output_item.done":
            item = payload.get("item", {})
            item_type = item.get("type")
            if item_type == "message":
                text = _stringify(item.get("content"))
                self.remember(text)
                events.append(self.emit("assistant_message", "Assistant message", text, payload))
            elif item_type == "function_call":
                events.append(self.emit("tool_call", f"Tool call: {item.get('name', 'tool')}", _stringify(item.get("arguments")), payload))
            else:
                events.append(self.emit("event", str(event_type), _stringify(payload), payload))
        elif event_type in {"item.completed", "item/completed"}:
            item = payload.get("item") or payload.get("params", {}).get("item") or {}
            text = _stringify(item)
            item_type = item.get("type") or item.get("details", {}).get("type") or "item"
            if item_type in {"agentMessage", "agent_message"} and text:
                self.remember(text)
            events.append(self.emit("item_completed", f"Item completed: {item_type}", text, payload))
        elif event_type in {"item.started", "item/started"}:
            item = payload.get("item") or payload.get("params", {}).get("item") or {}
            item_type = item.get("type") or item.get("details", {}).get("type") or "item"
            events.append(self.emit("item_started", f"Item started: {item_type}", _stringify(item), payload))
        elif event_type in {"response.completed", "turn/completed", "turn.completed"}:
            text = _stringify(payload.get("response") or payload.get("params") or payload)
            if text:
                self.remember(text)
            events.append(self.emit("completed", "Turn completed", text, payload))
        elif event_type in {"command/exec/outputDelta", "item/commandExecution/outputDelta"}:
            text = _stringify(payload.get("params") or payload)
            events.append(self.emit("command_output", "Command output", text, payload))
        else:
            events.append(self.emit("event", str(event_type), _stringify(payload), payload))
        return events


@dataclass(slots=True)
class ClaudeTraceParser(BaseTraceParser):
    def feed(self, line: str) -> list[NormalizedTraceEvent]:
        payload = _json(line)
        if payload is None:
            text = line.rstrip()
            self.remember(text)
            return [self.emit("stdout", "stdout", text, line)] if text else []

        event_type = payload.get("type") or "claude"
        if event_type == "system":
            subtype = str(payload.get("subtype") or "")
            if subtype.startswith("hook_"):
                if subtype in {"hook_error", "hook_failed"}:
                    content = _stringify(payload.get("error") or payload.get("stderr") or payload.get("output"))
                    title = f"Hook failed: {payload.get('hook_name', 'hook')}"
                    return [self.emit("hook_error", title, content, payload)]
                return []
        text = _stringify(payload.get("message") or payload.get("result") or payload.get("delta") or payload.get("content"))
        events: list[NormalizedTraceEvent] = []

        if event_type in {"assistant", "message"}:
            self.remember(text)
            events.append(self.emit("assistant_message", "Assistant message", text, payload))
        elif event_type in {"result", "final"}:
            if text and text != self.last_message:
                self.remember(text)
            events.append(self.emit("result", "Result", text, payload))
        elif event_type in {"tool_use", "tool_result"}:
            title = f"{event_type.replace('_', ' ').title()}"
            events.append(self.emit(event_type, title, text, payload))
        else:
            events.append(self.emit("event", str(event_type), text, payload))
        return events


@dataclass(slots=True)
class KimiTraceParser(BaseTraceParser):
    def feed(self, line: str) -> list[NormalizedTraceEvent]:
        payload = _json(line)
        if payload is None:
            text = line.rstrip()
            self.remember(text)
            return [self.emit("stdout", "stdout", text, line)] if text else []

        event_type = payload.get("type")
        inner = payload
        if payload.get("jsonrpc") == "2.0":
            event_type = payload.get("params", {}).get("type") or payload.get("method") or event_type
            inner = payload.get("params", {})
        payload_data = inner.get("payload") if isinstance(inner, dict) else None
        if payload_data is None and isinstance(inner, dict):
            payload_data = inner.get("result") or inner
        text = _stringify(payload_data)
        events: list[NormalizedTraceEvent] = []

        if event_type == "ContentPart":
            part_type = (payload_data or {}).get("type", "content")
            if part_type == "text":
                self.remember(_stringify(payload_data))
            events.append(self.emit(part_type, f"{part_type.title()} part", _stringify(payload_data), payload))
        elif event_type in {"ToolCall", "ToolResult", "StepBegin", "TurnBegin", "TurnEnd", "ApprovalRequest", "QuestionRequest", "MCPLoadingBegin", "MCPLoadingEnd"}:
            title = event_type.replace("_", " ")
            events.append(self.emit(event_type.lower(), title, text, payload))
        else:
            if text:
                self.remember(text)
            events.append(self.emit("event", str(event_type or "kimi"), text, payload))
        return events


@dataclass(slots=True)
class GenericTraceParser(BaseTraceParser):
    def feed(self, line: str) -> list[NormalizedTraceEvent]:
        text = line.rstrip()
        self.remember(text)
        return [self.emit("stdout", "stdout", text, line)] if text else []


def create_trace_parser(agent: AgentKind, node_id: str) -> BaseTraceParser:
    match agent:
        case AgentKind.CODEX:
            return CodexTraceParser(node_id=node_id, agent=agent)
        case AgentKind.CLAUDE:
            return ClaudeTraceParser(node_id=node_id, agent=agent)
        case AgentKind.KIMI:
            return KimiTraceParser(node_id=node_id, agent=agent)
    return GenericTraceParser(node_id=node_id, agent=agent)
