"""Explicit backend profiles and capability validation, independent of workflows."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentflow.specs import MCPServerSpec, ModelSettings, ProviderConfig, SecretRef, ToolAccess


class BackendCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    targets: frozenset[str] = frozenset({"docker"})
    capabilities: frozenset[str] = frozenset({"noninteractive", "secret_files", "skills"})
    model_settings: frozenset[str] = frozenset()
    provider_protocols: frozenset[str] = frozenset()


_COMMON = {"noninteractive", "secret_files", "skills", "read_write", "read_only"}
_ALL_TARGETS = frozenset({"local", "docker", "container", "ssh", "ec2", "ecs", "cloud_hypervisor"})
BACKEND_CAPABILITIES: dict[str, BackendCapabilities] = {
    "codex": BackendCapabilities(targets=_ALL_TARGETS, capabilities=_COMMON | {"mcp:stdio", "mcp:streamable_http", "mcp:timeouts"}, model_settings={"context_window", "max_output_tokens", "reasoning_effort", "web_search"}, provider_protocols={"responses"}),
    "claude": BackendCapabilities(targets=_ALL_TARGETS, capabilities=_COMMON | {"mcp:stdio", "mcp:streamable_http"}, model_settings={"max_output_tokens", "reasoning_effort", "max_turns", "web_search"}, provider_protocols={"anthropic", "messages"}),
    # The Kimi CLI runs with --yolo and has no tool allowlist, so a read_only node is refused at preparation (as for terminus).
    "kimi": BackendCapabilities(targets=_ALL_TARGETS, capabilities=(_COMMON - {"read_only"}) | {"mcp:stdio", "mcp:streamable_http"}, provider_protocols={"chat_completions"}),
    "pi": BackendCapabilities(targets=_ALL_TARGETS, capabilities=_COMMON | {"extensions"}, model_settings={"context_window", "max_output_tokens", "reasoning_effort", "web_search"}, provider_protocols={"openai-completions", "openai-responses", "anthropic-messages", "google-generative-ai"}),
    "terminus": BackendCapabilities(capabilities=_COMMON - {"read_only"}, model_settings={"context_window", "max_output_tokens", "reasoning_effort", "temperature", "max_turns", "web_search"}, provider_protocols={"responses", "chat_completions", "anthropic", "messages"}),
}


def register_backend_capabilities(agent: str, capabilities: BackendCapabilities) -> None:
    if agent in BACKEND_CAPABILITIES:
        raise ValueError(f"backend capabilities already registered: {agent}")
    BACKEND_CAPABILITIES[agent] = capabilities


class AgentProfile(BaseModel):
    """Serializable settings; credentials are always target-side file references."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = 1
    name: str
    agent: str
    model: str | None = None
    provider: ProviderConfig | None = None
    model_settings: ModelSettings = Field(default_factory=ModelSettings)
    mcps: list[MCPServerSpec] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=list)
    secret_env: dict[str, SecretRef] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=1800, gt=0)
    tools: ToolAccess = ToolAccess.READ_WRITE
    extra_args: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_version_and_secrets(self) -> "AgentProfile":
        if self.schema_version != 1:
            raise ValueError("unsupported agent profile schema version")
        if not self.name.strip() or not self.agent.strip():
            raise ValueError("agent profile name and agent are required")
        # All authentication belongs in references, never persisted profile values.
        if self.provider:
            if self.provider.api_key_env in self.provider.env:
                raise ValueError("provider credentials must use secret_env file references")
            for key in self.provider.headers:
                if key.lower() in {"authorization", "x-api-key", "api-key"}:
                    raise ValueError("provider authentication headers must use secret references")
        references = dict(self.secret_env)
        for mcp in self.mcps:
            for key in mcp.headers:
                if key.lower() in {"authorization", "x-api-key", "api-key"}:
                    raise ValueError("MCP authentication must use env_http_headers and secret_env")
            if set(mcp.env) & set(mcp.secret_env):
                raise ValueError("MCP secret_env conflicts with literal env")
            for name, reference in mcp.secret_env.items():
                if name in references and references[name] != reference:
                    raise ValueError(f"conflicting secret references for {name}")
                references[name] = reference
        return self

    @property
    def identity(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def validate_capabilities(self, *, target_kind: str = "docker", required_capabilities: Iterable[str] = ()) -> None:
        try:
            backend = BACKEND_CAPABILITIES[self.agent]
        except KeyError as exc:
            raise ValueError(f"unsupported agent backend: {self.agent}") from exc
        if target_kind not in backend.targets:
            raise ValueError(f"{self.agent} does not support target {target_kind}")
        required = set(required_capabilities) | {self.tools.value, "noninteractive"}
        required.update(f"mcp:{mcp.transport}" for mcp in self.mcps)
        if any(m.startup_timeout_sec or m.tool_timeout_sec for m in self.mcps):
            required.add("mcp:timeouts")
        if self.extensions:
            required.add("extensions")
        if self.skills:
            required.add("skills")
        if self.secret_env or any(m.secret_env for m in self.mcps):
            required.add("secret_files")
        unsupported = required - backend.capabilities
        if unsupported:
            raise ValueError(f"{self.agent} lacks required capabilities: {', '.join(sorted(unsupported))}")
        settings = self.model_settings.model_dump(exclude_none=True)
        unsupported_settings = set(settings) - backend.model_settings
        if unsupported_settings:
            raise ValueError(f"{self.agent} does not support model settings: {', '.join(sorted(unsupported_settings))}")
        if self.agent != "codex" and self.model_settings.web_search not in {None, "disabled"}:
            raise ValueError(f"{self.agent} supports only web_search=disabled")
        if self.agent == "terminus" and self.extra_args:
            raise ValueError("Terminus 2 uses typed model settings, not CLI extra_args")
        if self.agent == "pi" and (self.model_settings.context_window or self.model_settings.max_output_tokens):
            if not self.provider or not self.provider.base_url or not self.model:
                raise ValueError("Pi token limits require an explicit model and custom provider base_url")
        protocol = self.provider.wire_api if self.provider else None
        if protocol and protocol not in backend.provider_protocols:
            raise ValueError(f"{self.agent} does not support provider protocol: {protocol}")

    def node_options(self, *, target_kind: str = "docker", required_capabilities: Iterable[str] = ()) -> dict[str, Any]:
        self.validate_capabilities(target_kind=target_kind, required_capabilities=required_capabilities)
        values = self.model_dump(exclude={"schema_version", "name", "agent"})
        values["agent_profile"] = {"name": self.name, "agent": self.agent, "sha256": self.identity}
        return values
