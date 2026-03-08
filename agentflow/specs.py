from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class MCPServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    transport: Literal["stdio", "streamable_http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class LocalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["local"] = "local"
    cwd: str | None = None
    shell: str | None = None
    shell_login: bool = False
    shell_interactive: bool = False
    shell_init: str | None = None

    @model_validator(mode="after")
    def validate_shell_bootstrap(self) -> "LocalTarget":
        if self.shell and self.shell.strip():
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
        resolve_provider(self.provider, self.agent)
        return self


class PipelineSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    working_dir: str = "."
    concurrency: int = Field(default=4, ge=1)
    fail_fast: bool = False
    nodes: list[NodeSpec]

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
