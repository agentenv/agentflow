from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentflow.local_shell import invalid_bash_long_option_error, shell_wrapper_requires_command_placeholder


class AgentKind(StrEnum):
    CODEX = "codex"
    CLAUDE = "claude"
    KIMI = "kimi"


class ToolAccess(StrEnum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class CaptureMode(StrEnum):
    FINAL = "final"
    TRACE = "trace"


class NodeStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    READY = "ready"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class RunStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "default"
    base_url: str | None = None
    api_key_env: str | None = None
    wire_api: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)


_KIMI_ANTHROPIC_BASE_URL = "https://api.kimi.com/coding/"


def _normalized_provider_base_url(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped.rstrip("/")


def _normalized_provider_env_text(provider: ProviderConfig, key: str) -> str | None:
    raw_value = provider.env.get(key)
    if raw_value is None:
        return None
    stripped = str(raw_value).strip()
    if not stripped:
        return None
    return stripped


def _normalized_provider_env_base_url(provider: ProviderConfig, key: str) -> str | None:
    return _normalized_provider_base_url(_normalized_provider_env_text(provider, key))


def provider_uses_kimi_anthropic_auth(provider: ProviderConfig | None) -> bool:
    if provider is None:
        return False

    configured_api_key_env = (provider.api_key_env or "").strip()
    if not configured_api_key_env and _normalized_provider_env_text(provider, "ANTHROPIC_API_KEY") is not None:
        configured_api_key_env = "ANTHROPIC_API_KEY"
    if configured_api_key_env != "ANTHROPIC_API_KEY":
        return False

    effective_base_url = _normalized_provider_env_base_url(provider, "ANTHROPIC_BASE_URL")
    if effective_base_url is None:
        effective_base_url = _normalized_provider_base_url(provider.base_url)
    if effective_base_url is not None:
        return effective_base_url == _KIMI_ANTHROPIC_BASE_URL.rstrip("/")

    return (provider.name or "").strip().lower() == "kimi"


def resolve_provider(value: str | ProviderConfig | None, agent: AgentKind) -> ProviderConfig | None:
    if value is None:
        return None
    if isinstance(value, ProviderConfig):
        return value

    alias = value.strip().lower()
    if alias == "openai" and agent == AgentKind.CODEX:
        return ProviderConfig(
            name="openai",
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            wire_api="responses",
        )
    if alias == "anthropic" and agent == AgentKind.CLAUDE:
        return ProviderConfig(
            name="anthropic",
            base_url="https://api.anthropic.com",
            api_key_env="ANTHROPIC_API_KEY",
        )
    if alias in {"kimi", "moonshot", "moonshot-ai"}:
        if agent == AgentKind.CLAUDE:
            return ProviderConfig(
                name="kimi",
                base_url="https://api.kimi.com/coding/",
                api_key_env="ANTHROPIC_API_KEY",
            )
        if agent == AgentKind.KIMI:
            return ProviderConfig(
                name="moonshot",
                base_url="https://api.moonshot.ai/v1",
                api_key_env="KIMI_API_KEY",
            )
        raise ValueError(
            "provider 'kimi' is not supported for codex nodes because Codex requires an "
            "OpenAI Responses API backend and Kimi's public endpoints do not expose /responses"
        )
    return ProviderConfig(name=value)


def resolve_execution_provider(value: str | ProviderConfig | None, agent: AgentKind) -> ProviderConfig | None:
    provider = resolve_provider(value, agent)
    if provider is not None:
        return provider
    if agent == AgentKind.KIMI:
        return ProviderConfig(
            name="moonshot",
            base_url="https://api.moonshot.ai/v1",
            api_key_env="KIMI_API_KEY",
        )
    return None


class MCPServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    transport: Literal["stdio", "streamable_http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_transport_fields(self) -> "MCPServerSpec":
        if self.transport == "stdio":
            if not self.command or not self.command.strip():
                raise ValueError("stdio MCP servers require `command`")
            unsupported_fields = []
            if self.url and self.url.strip():
                unsupported_fields.append("url")
            if self.headers:
                unsupported_fields.append("headers")
        else:
            if not self.url or not self.url.strip():
                raise ValueError("streamable_http MCP servers require `url`")
            unsupported_fields = []
            if self.command and self.command.strip():
                unsupported_fields.append("command")
            if self.args:
                unsupported_fields.append("args")
            if self.env:
                unsupported_fields.append("env")

        if unsupported_fields:
            joined = ", ".join(f"`{field}`" for field in unsupported_fields)
            raise ValueError(f"{self.transport} MCP servers do not support {joined}")
        return self


class LocalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    _SHELL_COMMAND_PLACEHOLDER_MESSAGE = (
        "`target.shell` already includes a shell command payload. Add `{command}` where AgentFlow should "
        "inject the prepared agent command."
    )

    kind: Literal["local"] = "local"
    cwd: str | None = None
    shell: str | None = None
    shell_login: bool = False
    shell_interactive: bool = False
    shell_init: str | list[str] | None = None

    @field_validator("shell_init")
    @classmethod
    def validate_shell_init(cls, value: str | list[str] | None) -> str | list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("`target.shell_init` must not be empty")
            return normalized

        normalized_commands = [command.strip() for command in value if command.strip()]
        if not normalized_commands:
            raise ValueError("`target.shell_init` must contain at least one non-empty command")
        if len(normalized_commands) != len(value):
            raise ValueError("`target.shell_init` list entries must be non-empty strings")
        return normalized_commands

    @model_validator(mode="after")
    def validate_shell_bootstrap(self) -> "LocalTarget":
        if self.shell and self.shell.strip():
            invalid_option_error = invalid_bash_long_option_error(self.shell)
            if invalid_option_error is not None:
                raise ValueError(f"`target.shell` uses an unsupported bash long option. {invalid_option_error}")
            if shell_wrapper_requires_command_placeholder(self.shell):
                raise ValueError(self._SHELL_COMMAND_PLACEHOLDER_MESSAGE)
            return self

        missing_shell_fields: list[str] = []
        if self.shell_login:
            missing_shell_fields.append("shell_login")
        if self.shell_interactive:
            missing_shell_fields.append("shell_interactive")
        if self.shell_init:
            missing_shell_fields.append("shell_init")
        if missing_shell_fields:
            joined = ", ".join(f"`target.{field}`" for field in missing_shell_fields)
            raise ValueError(f"{joined} require `target.shell` on local targets")
        return self


class ContainerTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["container"] = "container"
    image: str
    engine: str = "docker"
    workdir_mount: str = "/workspace"
    runtime_mount: str = "/agentflow-runtime"
    app_mount: str = "/agentflow-app"
    extra_args: list[str] = Field(default_factory=list)
    entrypoint: str | None = None


class AwsLambdaTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["aws_lambda"] = "aws_lambda"
    function_name: str
    region: str | None = None
    remote_workdir: str = "/tmp/workspace"
    qualifier: str | None = None
    invocation_type: Literal["RequestResponse", "Event"] = "RequestResponse"


TargetSpec = Annotated[LocalTarget | ContainerTarget | AwsLambdaTarget, Field(discriminator="kind")]


class OutputContainsCriterion(BaseModel):
    kind: Literal["output_contains"] = "output_contains"
    value: str
    case_sensitive: bool = False


class FileExistsCriterion(BaseModel):
    kind: Literal["file_exists"] = "file_exists"
    path: str


class FileContainsCriterion(BaseModel):
    kind: Literal["file_contains"] = "file_contains"
    path: str
    value: str
    case_sensitive: bool = False


class FileNonEmptyCriterion(BaseModel):
    kind: Literal["file_nonempty"] = "file_nonempty"
    path: str


SuccessCriterion = Annotated[
    OutputContainsCriterion | FileExistsCriterion | FileContainsCriterion | FileNonEmptyCriterion,
    Field(discriminator="kind"),
]


class NodeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    agent: AgentKind
    prompt: str
    depends_on: list[str] = Field(default_factory=list)
    model: str | None = None
    provider: str | ProviderConfig | None = None
    tools: ToolAccess = ToolAccess.READ_ONLY
    mcps: list[MCPServerSpec] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    target: TargetSpec = Field(default_factory=LocalTarget)
    capture: CaptureMode = CaptureMode.FINAL
    output_key: str | None = None
    timeout_seconds: int = Field(default=1800, gt=0)
    env: dict[str, str] = Field(default_factory=dict)
    executable: str | None = None
    extra_args: list[str] = Field(default_factory=list)
    description: str | None = None
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    retries: int = Field(default=0, ge=0)
    retry_backoff_seconds: float = Field(default=1.0, ge=0.0)

    @model_validator(mode="after")
    def ensure_unique_dependencies(self) -> "NodeSpec":
        self.depends_on = list(dict.fromkeys(self.depends_on))
        duplicate_mcp_names = sorted(name for name, count in Counter(mcp.name for mcp in self.mcps).items() if count > 1)
        if duplicate_mcp_names:
            raise ValueError(f"duplicate MCP server names on node {self.id!r}: {duplicate_mcp_names}")
        resolve_provider(self.provider, self.agent)
        return self


def _local_target_defaults_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, LocalTarget):
        payload = value.model_dump(mode="python")
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        return None
    payload.setdefault("kind", "local")
    return payload


def apply_local_target_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(payload)
    local_target_defaults = _local_target_defaults_payload(resolved.get("local_target_defaults"))
    if local_target_defaults is None:
        return resolved

    nodes = resolved.get("nodes")
    if not isinstance(nodes, list):
        return resolved

    merged_nodes: list[Any] = []
    for node in nodes:
        if not isinstance(node, dict):
            merged_nodes.append(node)
            continue

        updated_node = dict(node)
        target = updated_node.get("target")
        if target is None:
            updated_node["target"] = dict(local_target_defaults)
            merged_nodes.append(updated_node)
            continue

        target_payload = _local_target_defaults_payload(target)
        if target_payload is None:
            merged_nodes.append(updated_node)
            continue

        if target_payload.get("kind", local_target_defaults.get("kind", "local")) != "local":
            merged_nodes.append(updated_node)
            continue

        merged_target = dict(local_target_defaults)
        merged_target.update(target_payload)
        updated_node["target"] = merged_target
        merged_nodes.append(updated_node)

    resolved["nodes"] = merged_nodes
    return resolved


class PipelineSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    working_dir: str = "."
    concurrency: int = Field(default=4, ge=1)
    fail_fast: bool = False
    local_target_defaults: LocalTarget | None = None
    nodes: list[NodeSpec]

    @model_validator(mode="before")
    @classmethod
    def apply_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return apply_local_target_defaults(data)

    @model_validator(mode="after")
    def validate_nodes(self) -> "PipelineSpec":
        ids = [node.id for node in self.nodes]
        duplicates = {node_id for node_id in ids if ids.count(node_id) > 1}
        if duplicates:
            raise ValueError(f"duplicate node ids: {sorted(duplicates)}")
        missing = {
            dependency
            for node in self.nodes
            for dependency in node.depends_on
            if dependency not in ids
        }
        if missing:
            raise ValueError(f"unknown dependencies: {sorted(missing)}")
        self._validate_acyclic_graph()
        return self

    def _validate_acyclic_graph(self) -> None:
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(node_id: str, graph: dict[str, NodeSpec]) -> None:
            if node_id in visiting:
                raise ValueError(f"cycle detected involving node {node_id!r}")
            if node_id in visited:
                return
            visiting.add(node_id)
            for dependency in graph[node_id].depends_on:
                visit(dependency, graph)
            visiting.remove(node_id)
            visited.add(node_id)

        graph = self.node_map
        for node_id in graph:
            visit(node_id, graph)

    @property
    def node_map(self) -> dict[str, NodeSpec]:
        return {node.id: node for node in self.nodes}

    @property
    def working_path(self) -> Path:
        return Path(self.working_dir).resolve()


class NormalizedTraceEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    node_id: str
    agent: AgentKind
    attempt: int = 1
    source: Literal["stdout", "stderr", "system"] = "stdout"
    kind: str
    title: str
    content: str | None = None
    raw: Any | None = None


class NodeAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int
    status: NodeStatus = NodeStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    final_response: str | None = None
    output: str | None = None
    success: bool | None = None
    success_details: list[str] = Field(default_factory=list)


class NodeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    status: NodeStatus = NodeStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    final_response: str | None = None
    output: str | None = None
    stdout_lines: list[str] = Field(default_factory=list)
    stderr_lines: list[str] = Field(default_factory=list)
    trace_events: list[NormalizedTraceEvent] = Field(default_factory=list)
    success: bool | None = None
    success_details: list[str] = Field(default_factory=list)
    current_attempt: int = 0
    attempts: list[NodeAttempt] = Field(default_factory=list)


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: RunStatus = RunStatus.QUEUED
    pipeline: PipelineSpec
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    nodes: dict[str, NodeResult] = Field(default_factory=dict)


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    run_id: str
    type: str
    node_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
