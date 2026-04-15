from __future__ import annotations

from agentflow.agents.base import AgentAdapter
from agentflow.agents.claude import ClaudeAdapter
from agentflow.agents.codex import CodexAdapter
from agentflow.agents.gemini import GeminiAdapter
from agentflow.agents.kimi import KimiAdapter
from agentflow.agents.util import PythonAdapter, ShellAdapter, SyncAdapter
from agentflow.specs import AgentKind


class AdapterRegistry:
    def __init__(self) -> None:
        self._registry: dict[AgentKind, AgentAdapter] = {
            AgentKind.CODEX: CodexAdapter(),
            AgentKind.CLAUDE: ClaudeAdapter(),
            AgentKind.KIMI: KimiAdapter(),
            AgentKind.GEMINI: GeminiAdapter(),
            AgentKind.PYTHON: PythonAdapter(),
            AgentKind.SHELL: ShellAdapter(),
            AgentKind.SYNC: SyncAdapter(),
        }

    def register(self, kind: AgentKind, adapter: AgentAdapter) -> None:
        self._registry[kind] = adapter

    def get(self, kind: AgentKind) -> AgentAdapter:
        return self._registry[kind]


default_adapter_registry = AdapterRegistry()
