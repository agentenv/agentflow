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

    def supports_raw_stdout_fallback(self) -> bool:
        return True


@dataclass(slots=True)
class CodexTraceParser(BaseTraceParser):
    def supports_raw_stdout_fallback(self) -> bool:
        return False

    def _is_ignorable_item_warning(self, item: dict[str, Any]) -> bool:
        item_type = item.get("type") or item.get("details", {}).get("type")
        if item_type != "error":
            return False
        message = str(item.get("message") or "")
        return message.startswith("Under-development features enabled:")

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
            if self._is_ignorable_item_warning(item):
                return []
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
    def supports_raw_stdout_fallback(self) -> bool:
        return False

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
    def supports_raw_stdout_fallback(self) -> bool:
        return False

    def _feed_message(self, payload: dict[str, Any]) -> list[NormalizedTraceEvent]:
        """Handle kimi CLI stream-json Message format (role/content)."""
        role = payload.get("role", "")
        events: list[NormalizedTraceEvent] = []
        if role == "assistant":
            content = payload.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        part_type = part.get("type", "text")
                        text = _stringify(part)
                        if part_type == "text" and text:
                            self.remember(text)
                        events.append(self.emit(part_type, f"{part_type.title()} part", text, payload))
            elif isinstance(content, str) and content:
                self.remember(content)
                events.append(self.emit("text", "Text part", content, payload))
            tool_calls = payload.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    name = fn.get("name", "tool")
                    events.append(self.emit("toolcall", f"ToolCall: {name}", _stringify(fn.get("arguments")), payload))
        elif role == "tool":
            text = _stringify(payload.get("content"))
            events.append(self.emit("toolresult", "ToolResult", text, payload))
        else:
            text = _stringify(payload)
            if text:
                self.remember(text)
            events.append(self.emit("event", str(role or "kimi"), text, payload))
        return events

    def feed(self, line: str) -> list[NormalizedTraceEvent]:
        payload = _json(line)
        if payload is None:
            text = line.rstrip()
            self.remember(text)
            return [self.emit("stdout", "stdout", text, line)] if text else []

        # kimi CLI stream-json outputs Message objects with a "role" field
        if "role" in payload:
            return self._feed_message(payload)

        # Legacy Wire protocol format (type/payload envelope, optionally wrapped in JSON-RPC 2.0)
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
class PiTraceParser(BaseTraceParser):
    """Parser for Pi CLI's ``--mode json`` event stream.

    Pi emits ordered events per turn: ``session``, ``agent_start``, ``turn_start``,
    ``message_start``, ``message_update`` (with ``text_delta`` / ``text_end`` sub-events),
    ``message_end``, ``turn_end``, ``agent_end``. The assistant text for the final
    turn is the authoritative output; we extract it from ``message_end`` and
    ``agent_end`` events, which carry the complete assembled content.
    """

    def supports_raw_stdout_fallback(self) -> bool:
        return False

    def _extract_text_from_content(self, content: Any) -> str:
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        if isinstance(content, str):
            return content
        return ""

    def feed(self, line: str) -> list[NormalizedTraceEvent]:
        payload = _json(line)
        if payload is None:
            text = line.rstrip()
            self.remember(text)
            return [self.emit("stdout", "stdout", text, line)] if text else []

        event_type = payload.get("type") or "pi"
        events: list[NormalizedTraceEvent] = []

        if event_type == "message_end":
            message = payload.get("message") or {}
            if message.get("role") == "assistant":
                text = self._extract_text_from_content(message.get("content"))
                if text:
                    self.remember(text)
                events.append(self.emit("assistant_message", "Assistant message", text, payload))
            return events

        if event_type == "agent_end":
            messages = payload.get("messages") or []
            final_text: str | None = None
            for message in messages:
                if isinstance(message, dict) and message.get("role") == "assistant":
                    text = self._extract_text_from_content(message.get("content"))
                    if text:
                        final_text = text
            if final_text and final_text != self.last_message:
                self.remember(final_text)
            events.append(self.emit("agent_end", "Agent end", final_text or "", payload))
            return events

        if event_type == "message_update":
            inner = payload.get("assistantMessageEvent") or {}
            sub_type = inner.get("type")
            if sub_type == "text_delta":
                delta = inner.get("delta")
                if isinstance(delta, str):
                    events.append(self.emit("assistant_delta", "Assistant delta", delta, payload))
            elif sub_type == "text_end":
                content = inner.get("content")
                if isinstance(content, str):
                    events.append(self.emit("assistant_text", "Assistant text", content, payload))
            return events

        if event_type == "turn_end":
            message = payload.get("message") or {}
            text = self._extract_text_from_content(message.get("content"))
            events.append(self.emit("turn_end", "Turn end", text, payload))
            return events

        if event_type in {"session", "agent_start", "turn_start", "message_start"}:
            events.append(self.emit(event_type, event_type.replace("_", " ").title(), None, payload))
            return events

        events.append(self.emit("event", str(event_type), _stringify(payload), payload))
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
        case AgentKind.PI:
            return PiTraceParser(node_id=node_id, agent=agent)
    return GenericTraceParser(node_id=node_id, agent=agent)
