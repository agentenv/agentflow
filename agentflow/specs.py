from __future__ import annotations

import csv
from copy import deepcopy
import json
import os
import re
import shlex
from collections import Counter
from datetime import datetime, timezone
from enum import StrEnum
from itertools import product
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import yaml

from agentflow.local_shell import (
    invalid_bash_long_option_error,
    shell_init_commands,
    shell_init_uses_kimi_helper,
    shell_wrapper_requires_command_placeholder,
    target_uses_bash,
    target_uses_login_bash,
    target_uses_interactive_bash,
    target_disables_bash_login_startup,
    target_disables_bash_rc_startup,
)


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
_LOCAL_KIMI_BOOTSTRAP_SHELL_INIT = ("command -v kimi >/dev/null 2>&1", "kimi")
_LOCAL_BOOTSTRAP_TARGET_KEYS = ("shell", "shell_login", "shell_interactive", "shell_init")
_FANOUT_ALIAS_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FANOUT_RESERVED_CONTEXT_NAMES = {"fanout", "fanouts", "nodes", "pipeline", "current"}
_FANOUT_MEMBER_RESERVED_NAMES = {"index", "number", "count", "suffix", "value", "template_id", "node_id"}
_FANOUT_TEMPLATE_PATTERN = re.compile(r"{{\s*(?P<expr>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*}}")
_FANOUT_EXPANSION_MODE_KEYS = ("count", "values", "values_path", "matrix", "matrix_path", "group_by", "batches")
_NODE_DEFAULT_FORBIDDEN_FIELDS = {
    "id",
    "prompt",
    "depends_on",
    "fanout",
    "fanout_group",
    "fanout_member",
    "fanout_dependencies",
}
_NODE_DEFAULT_LIST_MERGE_FIELDS = {"extra_args", "skills", "mcps"}
_NODE_DEFAULT_DICT_MERGE_FIELDS = {"env", "provider"}


def _normalize_local_bootstrap(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _local_bootstrap_defaults(bootstrap: str) -> dict[str, Any]:
    if bootstrap == "kimi":
        return {
            "shell": "bash",
            "shell_login": True,
            "shell_interactive": True,
            "shell_init": list(_LOCAL_KIMI_BOOTSTRAP_SHELL_INIT),
        }
    return {}


def _merge_bootstrap_shell_init(bootstrap: str, shell_init: Any) -> str | list[str] | None:
    defaults = _local_bootstrap_defaults(bootstrap)
    default_shell_init = defaults.get("shell_init")
    if default_shell_init is None:
        return shell_init
    if shell_init is None:
        return default_shell_init
    if bootstrap == "kimi" and shell_init_uses_kimi_helper(shell_init):
        return shell_init

    extra_commands = list(shell_init_commands(shell_init))
    if not extra_commands:
        return default_shell_init

    return [*extra_commands, *shell_init_commands(default_shell_init)]


def _normalized_provider_base_url(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped.rstrip("/")


def _shell_program(shell: str | None) -> str | None:
    if not isinstance(shell, str) or not shell.strip():
        return None
    try:
        parts = shlex.split(shell)
    except ValueError:
        return None
    if not parts:
        return None
    return os.path.basename(parts[0]) or None


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


def _coerce_base_dir(value: object) -> Path | None:
    if isinstance(value, Path):
        return value.expanduser().resolve()
    if isinstance(value, str) and value.strip():
        return Path(value).expanduser().resolve()
    return None


def provider_uses_kimi_anthropic_auth(provider: ProviderConfig | None) -> bool:
    if provider is None:
        return False

    effective_base_url = _normalized_provider_env_base_url(provider, "ANTHROPIC_BASE_URL")
    if effective_base_url is None:
        effective_base_url = _normalized_provider_base_url(provider.base_url)
    if effective_base_url is not None:
        return effective_base_url == _KIMI_ANTHROPIC_BASE_URL.rstrip("/")

    return (provider.name or "").strip().lower() in {"kimi", "moonshot", "moonshot-ai"}


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
    bootstrap: str | None = None
    shell: str | None = None
    shell_login: bool = False
    shell_interactive: bool = False
    shell_init: str | list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def apply_bootstrap_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        bootstrap = _normalize_local_bootstrap(data.get("bootstrap"))
        if bootstrap is None:
            return data

        updated = dict(data)
        for key, value in _local_bootstrap_defaults(bootstrap).items():
            if key == "shell_init":
                updated[key] = _merge_bootstrap_shell_init(bootstrap, updated.get(key))
                continue
            if key not in updated or updated[key] is None:
                updated[key] = value
        return updated

    @field_validator("bootstrap")
    @classmethod
    def validate_bootstrap(cls, value: str | None) -> str | None:
        normalized = _normalize_local_bootstrap(value)
        if normalized is None:
            return None
        if normalized != "kimi":
            raise ValueError("`target.bootstrap` must be `kimi`")
        return normalized

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
        else:
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

        if self.bootstrap == "kimi":
            target_shell = _shell_program(self.shell) or "this shell"
            if not target_uses_bash(self):
                raise ValueError(
                    f"`target.bootstrap: kimi` requires bash-style shell bootstrap, but `target.shell` resolves "
                    f"to `{target_shell}`. Use `shell: bash` with `target.shell_interactive: true`, use `bash -lic`, "
                    "or drop `target.bootstrap` and configure the bootstrap explicitly."
                )
            if not target_uses_interactive_bash(self):
                raise ValueError(
                    "`target.bootstrap: kimi` requires interactive bash startup so helpers from `~/.bashrc` are "
                    "available. Set `target.shell_interactive: true`, use `bash -lic`, or drop `target.bootstrap` "
                    "and configure the bootstrap explicitly."
                )
            if target_uses_login_bash(self) and target_disables_bash_login_startup(self):
                raise ValueError(
                    "`target.bootstrap: kimi` cannot use bash with `--noprofile` because login startup files will "
                    "not load the `kimi` helper. Remove `--noprofile` or drop `target.bootstrap` and configure the "
                    "bootstrap explicitly."
                )
            if not target_uses_login_bash(self) and target_disables_bash_rc_startup(self):
                raise ValueError(
                    "`target.bootstrap: kimi` cannot use bash with `--norc` because interactive startup will not "
                    "load `~/.bashrc` and the `kimi` helper will usually be unavailable. Remove `--norc` or drop "
                    "`target.bootstrap` and configure the bootstrap explicitly."
                )
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


class FanoutGroupBySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    fields: list[str]

    @field_validator("from_")
    @classmethod
    def validate_source_group(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("`fanout.group_by.from` must not be empty")
        return normalized

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("`fanout.group_by.fields` must contain at least one field")

        normalized: list[str] = []
        seen: set[str] = set()
        for raw_field in value:
            if not isinstance(raw_field, str):
                raise ValueError("`fanout.group_by.fields` entries must be strings")
            field = raw_field.strip()
            if not field:
                raise ValueError("`fanout.group_by.fields` entries must not be empty")
            if not _FANOUT_ALIAS_PATTERN.fullmatch(field):
                raise ValueError("`fanout.group_by.fields` entries must be valid member field names")
            if field in seen:
                raise ValueError(f"`fanout.group_by.fields` contains duplicate field `{field}`")
            seen.add(field)
            normalized.append(field)
        return normalized


class FanoutBatchesSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    size: int = Field(gt=0)

    @field_validator("from_")
    @classmethod
    def validate_source_group(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("`fanout.batches.from` must not be empty")
        return normalized


class FanoutSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    count: int | None = Field(default=None, ge=1)
    values: list[Any] | None = None
    matrix: dict[str, list[Any]] | None = None
    include: list[dict[str, Any]] | None = None
    exclude: list[dict[str, Any]] | None = None
    derive: dict[str, Any] = Field(default_factory=dict)
    as_: str = Field(default="item", alias="as")

    @field_validator("values")
    @classmethod
    def validate_values(cls, value: list[Any] | None) -> list[Any] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("`fanout.values` must contain at least one item")
        return value

    @field_validator("matrix")
    @classmethod
    def validate_matrix(cls, value: dict[str, list[Any]] | None) -> dict[str, list[Any]] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("`fanout.matrix` must contain at least one axis")

        normalized: dict[str, list[Any]] = {}
        for axis_name, axis_values in value.items():
            axis = axis_name.strip()
            if not axis:
                raise ValueError("`fanout.matrix` axis names must not be empty")
            if not _FANOUT_ALIAS_PATTERN.fullmatch(axis):
                raise ValueError("`fanout.matrix` axis names must be valid template variable names")
            if axis in _FANOUT_MEMBER_RESERVED_NAMES:
                raise ValueError(
                    "`fanout.matrix` axis names must not use reserved member fields such as "
                    "`index`, `number`, `count`, `suffix`, `value`, `template_id`, or `node_id`"
                )
            if axis in normalized:
                raise ValueError(f"`fanout.matrix` axis `{axis}` was provided more than once")
            if not axis_values:
                raise ValueError(f"`fanout.matrix.{axis}` must contain at least one item")
            normalized[axis] = axis_values
        return normalized

    @field_validator("include")
    @classmethod
    def validate_include(cls, value: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("`fanout.include` must contain at least one item")
        return [_normalize_fanout_matrix_member(item) for item in value]

    @field_validator("exclude")
    @classmethod
    def validate_exclude(cls, value: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("`fanout.exclude` must contain at least one item")
        return value

    @field_validator("derive")
    @classmethod
    def validate_derive(cls, value: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for field_name, field_value in value.items():
            if not isinstance(field_name, str):
                raise ValueError("`fanout.derive` field names must be strings")
            field = field_name.strip()
            if not field:
                raise ValueError("`fanout.derive` field names must not be empty")
            if not _FANOUT_ALIAS_PATTERN.fullmatch(field):
                raise ValueError("`fanout.derive` field names must be valid template variable names")
            if field in _FANOUT_MEMBER_RESERVED_NAMES:
                raise ValueError(
                    "`fanout.derive` field names must not use reserved member fields such as "
                    "`index`, `number`, `count`, `suffix`, `value`, `template_id`, or `node_id`"
                )
            normalized[field] = field_value
        return normalized

    @field_validator("as_")
    @classmethod
    def validate_alias(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("`fanout.as` must not be empty")
        if normalized in _FANOUT_RESERVED_CONTEXT_NAMES:
            raise ValueError(
                "`fanout.as` uses a reserved template variable name; choose something other than "
                "`fanout`, `fanouts`, `nodes`, `pipeline`, or `current`"
            )
        if not _FANOUT_ALIAS_PATTERN.fullmatch(normalized):
            raise ValueError("`fanout.as` must be a valid template variable name")
        return normalized

    @model_validator(mode="after")
    def validate_shape(self) -> "FanoutSpec":
        modes = (
            self.count is not None,
            self.values is not None,
            self.matrix is not None,
        )
        selected = sum(modes)
        if selected == 0:
            raise ValueError("fanout requires exactly one of `count`, `values`, or `matrix`")
        if selected > 1:
            raise ValueError("fanout accepts exactly one of `count`, `values`, or `matrix`")
        if (self.include is not None or self.exclude is not None) and self.matrix is None:
            raise ValueError("`fanout.include` and `fanout.exclude` require `fanout.matrix` or `fanout.matrix_path`")
        if self.matrix is not None and not _curate_fanout_matrix_members(
            self.matrix,
            include=self.include,
            exclude=self.exclude,
        ):
            raise ValueError("`fanout.matrix` produced no members after applying `fanout.exclude`")
        return self

    @property
    def member_values(self) -> list[Any]:
        if self.values is not None:
            return self.values
        if self.matrix is not None:
            return _curate_fanout_matrix_members(self.matrix, include=self.include, exclude=self.exclude)
        if self.count is None:
            return []
        return list(range(self.count))

    @property
    def member_count(self) -> int:
        if self.values is not None:
            return len(self.values)
        if self.matrix is not None:
            return len(_curate_fanout_matrix_members(self.matrix, include=self.include, exclude=self.exclude))
        if self.count is None:
            return 0
        return self.count


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
    fanout_group: str | None = Field(default=None, exclude=True)
    fanout_member: dict[str, Any] | None = Field(default=None, exclude=True)
    fanout_dependencies: dict[str, list[str]] = Field(default_factory=dict, exclude=True)

    @model_validator(mode="after")
    def ensure_unique_dependencies(self) -> "NodeSpec":
        self.depends_on = list(dict.fromkeys(self.depends_on))
        duplicate_mcp_names = sorted(name for name, count in Counter(mcp.name for mcp in self.mcps).items() if count > 1)
        if duplicate_mcp_names:
            raise ValueError(f"duplicate MCP server names on node {self.id!r}: {duplicate_mcp_names}")
        resolve_provider(self.provider, self.agent)
        return self


def _fanout_suffix(index: int, count: int) -> str:
    width = max(1, len(str(count)))
    return str(index).zfill(width)


def _lift_fanout_member_mapping(
    member: dict[str, Any],
    mapping: dict[str, Any],
    *,
    strict: bool = False,
    source: str | None = None,
) -> None:
    for key, item in mapping.items():
        if not isinstance(key, str) or not _FANOUT_ALIAS_PATTERN.fullmatch(key):
            continue
        if key in _FANOUT_MEMBER_RESERVED_NAMES:
            if strict:
                axis_label = f" axis `{source}`" if source else ""
                raise ValueError(
                    f"fanout.matrix{axis_label} item uses reserved lifted key `{key}`; "
                    "choose a different key name"
                )
            continue
        if key in member:
            if strict and member[key] != item:
                axis_label = f" axis `{source}`" if source else ""
                raise ValueError(
                    f"fanout.matrix{axis_label} item conflicts on lifted key `{key}`; "
                    "use distinct field names across axes"
                )
            continue
        member[key] = item


def _expand_fanout_matrix(matrix: dict[str, list[Any]]) -> list[dict[str, Any]]:
    axis_names = list(matrix)
    axis_values = [matrix[axis_name] for axis_name in axis_names]
    members: list[dict[str, Any]] = []
    for combination in product(*axis_values):
        member: dict[str, Any] = {}
        for axis_name, axis_value in zip(axis_names, combination):
            if axis_name in member and member[axis_name] != axis_value:
                raise ValueError(
                    f"fanout.matrix axis `{axis_name}` conflicts with another lifted field; "
                    "rename the axis or the conflicting field"
                )
            member[axis_name] = axis_value
            if isinstance(axis_value, dict):
                _lift_fanout_member_mapping(member, axis_value, strict=True, source=axis_name)
        members.append(member)
    return members


def _normalize_fanout_matrix_member(value: dict[str, Any]) -> dict[str, Any]:
    member = dict(value)
    for key, item in value.items():
        if isinstance(item, dict):
            _lift_fanout_member_mapping(member, item, strict=True, source=key)
    return member


def _fanout_member_matches_selector(member: Any, selector: Any) -> bool:
    if isinstance(selector, dict):
        if not isinstance(member, dict):
            return False
        return all(
            key in member and _fanout_member_matches_selector(member[key], expected)
            for key, expected in selector.items()
        )
    return member == selector


def _curate_fanout_matrix_members(
    matrix: dict[str, list[Any]],
    *,
    include: list[dict[str, Any]] | None = None,
    exclude: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    members = _expand_fanout_matrix(matrix)
    if exclude:
        members = [
            member
            for member in members
            if not any(_fanout_member_matches_selector(member, selector) for selector in exclude)
        ]
    if include:
        members.extend(dict(member) for member in include)
    return members


def _freeze_fanout_value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((key, _freeze_fanout_value(item)) for key, item in sorted(value.items()))
    if isinstance(value, list):
        return tuple(_freeze_fanout_value(item) for item in value)
    return value


def _resolve_grouped_fanout_members(
    group_by: FanoutGroupBySpec,
    *,
    source_members: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    members = source_members.get(group_by.from_)
    if members is None:
        raise ValueError(
            f"`fanout.group_by.from` references unknown prior fanout group `{group_by.from_}`; "
            "place the source fanout earlier in the pipeline"
        )

    grouped_members: list[dict[str, Any]] = []
    grouped_indexes: dict[Any, int] = {}
    scoped_metadata_fields = {"source_group", "source_count", "size", "member_ids", "members"}
    source_count = len(members)
    for member in members:
        grouped_member: dict[str, Any] = {}
        for field in group_by.fields:
            if field not in member:
                raise ValueError(
                    f"`fanout.group_by.fields` references `{field}`, but fanout group `{group_by.from_}` "
                    "does not expose that field"
                )
            grouped_member[field] = member[field]

        conflicting_fields = sorted(scoped_metadata_fields.intersection(grouped_member))
        if conflicting_fields:
            joined = ", ".join(f"`{field}`" for field in conflicting_fields)
            raise ValueError(
                f"`fanout.group_by.fields` cannot use reserved scoped reducer metadata fields {joined}"
            )

        frozen = _freeze_fanout_value(grouped_member)
        node_id = member.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError(
                f"fanout group `{group_by.from_}` does not expose `node_id`, so `fanout.group_by` "
                "cannot derive scoped reducer dependencies"
            )

        grouped_index = grouped_indexes.get(frozen)
        if grouped_index is None:
            grouped_indexes[frozen] = len(grouped_members)
            grouped_members.append(
                {
                    "source_group": group_by.from_,
                    "source_count": source_count,
                    "size": 1,
                    "member_ids": [node_id],
                    "members": [dict(member)],
                    **grouped_member,
                }
            )
            continue

        grouped_members[grouped_index]["size"] += 1
        grouped_members[grouped_index]["member_ids"].append(node_id)
        grouped_members[grouped_index]["members"].append(dict(member))
    return grouped_members


def _resolve_batched_fanout_members(
    batches: FanoutBatchesSpec,
    *,
    source_members: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    members = source_members.get(batches.from_)
    if members is None:
        raise ValueError(
            f"`fanout.batches.from` references unknown prior fanout group `{batches.from_}`; "
            "place the source fanout earlier in the pipeline"
        )

    batched_members: list[dict[str, Any]] = []
    source_count = len(members)
    for offset in range(0, source_count, batches.size):
        batch_members = [dict(member) for member in members[offset : offset + batches.size]]
        if not batch_members:
            continue

        member_ids: list[str] = []
        for member in batch_members:
            node_id = member.get("node_id")
            if not isinstance(node_id, str) or not node_id:
                raise ValueError(
                    f"fanout group `{batches.from_}` does not expose `node_id`, so `fanout.batches` "
                    "cannot derive reducer dependencies"
                )
            member_ids.append(node_id)

        first = batch_members[0]
        last = batch_members[-1]
        batched_members.append(
            {
                "source_group": batches.from_,
                "source_count": source_count,
                "size": len(batch_members),
                "member_ids": member_ids,
                "members": batch_members,
                "start_index": first["index"],
                "end_index": last["index"],
                "start_number": first["number"],
                "end_number": last["number"],
                "start_suffix": first["suffix"],
                "end_suffix": last["suffix"],
            }
        )
    return batched_members


def _fanout_dependency_overrides(member: dict[str, Any]) -> dict[str, list[str]]:
    source_group = member.get("source_group")
    member_ids = member.get("member_ids")
    if not isinstance(source_group, str) or not source_group:
        return {}
    if not isinstance(member_ids, list):
        return {}

    scoped_member_ids = [member_id for member_id in member_ids if isinstance(member_id, str) and member_id]
    if not scoped_member_ids:
        return {}
    return {source_group: scoped_member_ids}


def _fanout_iteration_context(template_id: str, fanout: FanoutSpec, index: int, value: Any) -> dict[str, Any]:
    member_count = fanout.member_count
    suffix = _fanout_suffix(index, member_count)
    member = {
        "index": index,
        "number": index + 1,
        "count": member_count,
        "suffix": suffix,
        "value": value,
        "template_id": template_id,
        "node_id": f"{template_id}_{suffix}",
    }
    if isinstance(value, dict):
        _lift_fanout_member_mapping(member, value)
    context = {fanout.as_: member, "fanout": member}
    for key, raw_value in fanout.derive.items():
        if key in member:
            raise ValueError(
                f"fanout.derive field `{key}` conflicts with an existing member field; choose a different name"
            )
        member[key] = _render_fanout_value(raw_value, context)
    return context


def _render_fanout_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _render_fanout_string(value, context)
    if isinstance(value, list):
        return [_render_fanout_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render_fanout_value(item, context) for key, item in value.items()}
    return value


def _resolve_fanout_template_expression(context: dict[str, Any], expression: str) -> Any:
    current: Any = context
    for part in expression.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        raise KeyError(expression)
    return current


def _render_fanout_string(template_text: str, context: dict[str, Any]) -> str:
    def _replace(match: re.Match[str]) -> str:
        expression = match.group("expr")
        root = expression.split(".", 1)[0]
        if root not in context:
            return match.group(0)
        try:
            resolved = _resolve_fanout_template_expression(context, expression)
        except KeyError:
            return match.group(0)
        return str(resolved)

    return _FANOUT_TEMPLATE_PATTERN.sub(_replace, template_text)


def _resolve_fanout_manifest_path(raw_path: object, *, key: str, base_dir: Path | None) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"`fanout.{key}` must be a non-empty path")

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = ((base_dir or Path.cwd()) / path).resolve()
    else:
        path = path.resolve()

    if not path.exists():
        raise ValueError(f"`fanout.{key}` path `{path}` does not exist")
    if not path.is_file():
        raise ValueError(f"`fanout.{key}` path `{path}` is not a file")
    return path


def _load_fanout_manifest_payload(path: Path) -> Any:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError(f"fanout manifest `{path}` must include a CSV header row")
            return [dict(row) for row in reader]

    data = path.read_text(encoding="utf-8")
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        try:
            return yaml.safe_load(data)
        except yaml.YAMLError as exc:
            raise ValueError(f"fanout manifest `{path}` could not be parsed as JSON or YAML") from exc


def _resolve_fanout_manifest_modes(raw_fanout: Any, *, base_dir: Path | None) -> Any:
    if not isinstance(raw_fanout, dict):
        return raw_fanout

    updated = dict(raw_fanout)
    selected_modes = [key for key in _FANOUT_EXPANSION_MODE_KEYS if updated.get(key) is not None]
    if len(selected_modes) > 1:
        joined = ", ".join(f"`{key}`" for key in _FANOUT_EXPANSION_MODE_KEYS)
        raise ValueError(f"fanout accepts exactly one of {joined}")

    values_path = updated.pop("values_path", None)
    if values_path is not None:
        path = _resolve_fanout_manifest_path(values_path, key="values_path", base_dir=base_dir)
        values = _load_fanout_manifest_payload(path)
        if not isinstance(values, list):
            raise ValueError(
                f"`fanout.values_path` file `{path}` must contain a list in JSON/YAML or rows in CSV format"
            )
        updated["values"] = values

    matrix_path = updated.pop("matrix_path", None)
    if matrix_path is not None:
        path = _resolve_fanout_manifest_path(matrix_path, key="matrix_path", base_dir=base_dir)
        if path.suffix.lower() == ".csv":
            raise ValueError("`fanout.matrix_path` does not support CSV; use JSON or YAML")
        matrix = _load_fanout_manifest_payload(path)
        if not isinstance(matrix, dict):
            raise ValueError(f"`fanout.matrix_path` file `{path}` must contain a JSON/YAML object")
        updated["matrix"] = matrix

    return updated


def _resolve_fanout_source_modes(raw_fanout: Any, *, source_members: dict[str, list[dict[str, Any]]]) -> Any:
    if not isinstance(raw_fanout, dict):
        return raw_fanout

    updated = dict(raw_fanout)
    raw_group_by = updated.pop("group_by", None)
    raw_batches = updated.pop("batches", None)
    if raw_group_by is not None and raw_batches is not None:
        joined = ", ".join(f"`{key}`" for key in _FANOUT_EXPANSION_MODE_KEYS)
        raise ValueError(f"fanout accepts exactly one of {joined}")

    if raw_group_by is not None:
        group_by = FanoutGroupBySpec.model_validate(raw_group_by)
        updated["values"] = _resolve_grouped_fanout_members(group_by, source_members=source_members)

    if raw_batches is not None:
        batches = FanoutBatchesSpec.model_validate(raw_batches)
        updated["values"] = _resolve_batched_fanout_members(batches, source_members=source_members)
    return updated


def _expand_fanout_node(node: dict[str, Any], fanout: FanoutSpec) -> tuple[list[dict[str, Any]], list[str]]:
    template_id = node.get("id")
    if not isinstance(template_id, str) or not template_id.strip():
        raise ValueError("fanout nodes require a non-empty string `id`")
    if any(marker in template_id for marker in ("{{", "{%", "{#")):
        raise ValueError("fanout node `id` must be a literal group name, not a rendered template")

    node_template = dict(node)
    node_template.pop("fanout", None)
    expanded_nodes: list[dict[str, Any]] = []
    member_ids: list[str] = []
    for index, value in enumerate(fanout.member_values):
        iteration_context = _fanout_iteration_context(template_id, fanout, index, value)
        expanded = _render_fanout_value(node_template, iteration_context)
        if not isinstance(expanded, dict):
            raise ValueError(f"fanout node {template_id!r} did not expand into an object")
        member_id = iteration_context["fanout"]["node_id"]
        expanded["id"] = member_id
        expanded["fanout_group"] = template_id
        expanded["fanout_member"] = dict(iteration_context["fanout"])
        fanout_dependencies = _fanout_dependency_overrides(iteration_context["fanout"])
        if fanout_dependencies:
            expanded["fanout_dependencies"] = fanout_dependencies
        expanded_nodes.append(expanded)
        member_ids.append(member_id)
    return expanded_nodes, member_ids


def _expand_fanout_dependencies(nodes: list[Any], fanouts: dict[str, list[str]]) -> list[Any]:
    expanded_nodes: list[Any] = []
    for node in nodes:
        if not isinstance(node, dict):
            expanded_nodes.append(node)
            continue
        depends_on = node.get("depends_on")
        if not isinstance(depends_on, list):
            expanded_nodes.append(node)
            continue
        updated = dict(node)
        dependency_overrides = updated.get("fanout_dependencies")
        rewritten: list[Any] = []
        for dependency in depends_on:
            if isinstance(dependency, str) and dependency in fanouts:
                if isinstance(dependency_overrides, dict):
                    scoped_members = dependency_overrides.get(dependency)
                    if isinstance(scoped_members, list) and scoped_members:
                        rewritten.extend(scoped_members)
                        continue
                rewritten.extend(fanouts[dependency])
                continue
            rewritten.append(dependency)
        updated["depends_on"] = rewritten
        expanded_nodes.append(updated)
    return expanded_nodes


def expand_compact_nodes(payload: dict[str, Any], *, base_dir: str | Path | None = None) -> dict[str, Any]:
    resolved = dict(payload)
    nodes = resolved.get("nodes")
    if not isinstance(nodes, list):
        return resolved
    resolved_base_dir = _coerce_base_dir(base_dir)

    source_ids = [node.get("id") for node in nodes if isinstance(node, dict) and isinstance(node.get("id"), str)]
    duplicate_source_ids = {node_id for node_id, count in Counter(source_ids).items() if count > 1}
    if duplicate_source_ids:
        raise ValueError(f"duplicate node ids: {sorted(duplicate_source_ids)}")

    fanouts: dict[str, list[str]] = {}
    raw_fanouts = resolved.get("fanouts")
    if isinstance(raw_fanouts, dict):
        fanouts = {
            str(group_id): [str(member_id) for member_id in members]
            for group_id, members in raw_fanouts.items()
            if isinstance(group_id, str) and isinstance(members, list)
        }
    saw_fanout = False
    expanded_nodes: list[Any] = []
    fanout_members: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            expanded_nodes.append(node)
            continue
        raw_fanout = node.get("fanout")
        if raw_fanout is None:
            expanded_nodes.append(dict(node))
            continue
        saw_fanout = True
        resolved_fanout = _resolve_fanout_manifest_modes(raw_fanout, base_dir=resolved_base_dir)
        resolved_fanout = _resolve_fanout_source_modes(resolved_fanout, source_members=fanout_members)
        fanout = FanoutSpec.model_validate(resolved_fanout)
        rendered_nodes, member_ids = _expand_fanout_node(node, fanout)
        fanouts[str(node.get("id"))] = member_ids
        fanout_members[str(node.get("id"))] = [
            dict(rendered_node["fanout_member"])
            for rendered_node in rendered_nodes
            if isinstance(rendered_node, dict) and isinstance(rendered_node.get("fanout_member"), dict)
        ]
        expanded_nodes.extend(rendered_nodes)

    if not saw_fanout:
        return resolved

    resolved["fanouts"] = fanouts
    resolved["nodes"] = _expand_fanout_dependencies(expanded_nodes, fanouts)
    return resolved


def _local_target_defaults_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, LocalTarget):
        payload = value.model_dump(mode="python")
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        return None
    payload.setdefault("kind", "local")
    return payload


def _node_default_payload(
    value: Any,
    *,
    subject: str,
    allow_agent: bool,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"`{subject}` must be an object")

    allowed = set(NodeSpec.model_fields) - _NODE_DEFAULT_FORBIDDEN_FIELDS
    if not allow_agent:
        allowed.discard("agent")

    unknown = sorted(set(value) - allowed)
    if unknown:
        supported = ", ".join(f"`{field}`" for field in sorted(allowed))
        unknown_display = ", ".join(f"`{field}`" for field in unknown)
        raise ValueError(f"`{subject}` does not support {unknown_display}; supported fields: {supported}")

    return dict(value)


def _merge_default_target_payload(default_value: Any, override_value: Any) -> Any:
    if not isinstance(default_value, dict) or not isinstance(override_value, dict):
        return deepcopy(override_value)

    default_kind = default_value.get("kind")
    override_kind = override_value.get("kind")
    if default_kind and override_kind and default_kind != override_kind:
        return deepcopy(override_value)

    merged = deepcopy(default_value)
    merged.update(deepcopy(override_value))
    return merged


def _merge_node_payloads(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(defaults)
    for key, value in overrides.items():
        if key == "target":
            merged[key] = _merge_default_target_payload(merged.get(key), value)
            continue
        if (
            key in _NODE_DEFAULT_LIST_MERGE_FIELDS
            and isinstance(merged.get(key), list)
            and isinstance(value, list)
        ):
            merged[key] = [*deepcopy(merged[key]), *deepcopy(value)]
            continue
        if (
            key in _NODE_DEFAULT_DICT_MERGE_FIELDS
            and isinstance(merged.get(key), dict)
            and isinstance(value, dict)
        ):
            merged[key] = {**deepcopy(merged[key]), **deepcopy(value)}
            continue
        merged[key] = deepcopy(value)
    return merged


def apply_node_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(payload)
    node_defaults = _node_default_payload(
        resolved.get("node_defaults"),
        subject="node_defaults",
        allow_agent=True,
    )
    raw_agent_defaults = resolved.get("agent_defaults")
    if raw_agent_defaults is None:
        agent_defaults: dict[AgentKind, dict[str, Any]] = {}
    else:
        if not isinstance(raw_agent_defaults, dict):
            raise ValueError("`agent_defaults` must be an object keyed by agent name")
        agent_defaults = {}
        for raw_agent, defaults in raw_agent_defaults.items():
            try:
                agent = raw_agent if isinstance(raw_agent, AgentKind) else AgentKind(str(raw_agent).strip())
            except ValueError as exc:
                supported = ", ".join(f"`{agent.value}`" for agent in AgentKind)
                raise ValueError(f"`agent_defaults` has unknown agent `{raw_agent}`; supported keys: {supported}") from exc
            normalized = _node_default_payload(
                defaults,
                subject=f"agent_defaults.{agent.value}",
                allow_agent=False,
            )
            if normalized is not None:
                agent_defaults[agent] = normalized

    if node_defaults is None and not agent_defaults:
        return resolved

    nodes = resolved.get("nodes")
    if not isinstance(nodes, list):
        return resolved

    merged_nodes: list[Any] = []
    for node in nodes:
        if not isinstance(node, dict):
            merged_nodes.append(node)
            continue

        merged_node = deepcopy(node_defaults or {})
        raw_agent = node.get("agent", merged_node.get("agent"))
        if raw_agent is not None:
            agent = raw_agent if isinstance(raw_agent, AgentKind) else AgentKind(str(raw_agent).strip())
            merged_node = _merge_node_payloads(merged_node, agent_defaults.get(agent, {}))
        merged_nodes.append(_merge_node_payloads(merged_node, dict(node)))

    resolved["nodes"] = merged_nodes
    if node_defaults is not None:
        resolved["node_defaults"] = node_defaults
    if agent_defaults:
        resolved["agent_defaults"] = {agent.value: defaults for agent, defaults in agent_defaults.items()}
    return resolved


def _target_disables_inherited_bootstrap(target_payload: dict[str, Any]) -> bool:
    if "bootstrap" not in target_payload:
        return False
    return _normalize_local_bootstrap(target_payload.get("bootstrap")) is None


def _drop_inherited_bootstrap_defaults(local_target_defaults: dict[str, Any]) -> dict[str, Any]:
    inherited = dict(local_target_defaults)
    bootstrap = _normalize_local_bootstrap(inherited.get("bootstrap"))
    if bootstrap is None:
        return inherited

    inherited.pop("bootstrap", None)
    for key in _LOCAL_BOOTSTRAP_TARGET_KEYS:
        inherited.pop(key, None)
    return inherited


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

        merged_target = (
            _drop_inherited_bootstrap_defaults(local_target_defaults)
            if _target_disables_inherited_bootstrap(target_payload)
            else dict(local_target_defaults)
        )
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
    node_defaults: dict[str, Any] | None = None
    agent_defaults: dict[AgentKind, dict[str, Any]] = Field(default_factory=dict)
    local_target_defaults: LocalTarget | None = None
    fanouts: dict[str, list[str]] = Field(default_factory=dict)
    nodes: list[NodeSpec]

    @model_validator(mode="before")
    @classmethod
    def apply_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        base_dir = payload.pop("base_dir", None)
        expanded = expand_compact_nodes(payload, base_dir=base_dir)
        expanded = apply_node_defaults(expanded)
        return apply_local_target_defaults(expanded)

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
        fanout_missing = {
            member_id
            for members in self.fanouts.values()
            for member_id in members
            if member_id not in ids
        }
        if fanout_missing:
            raise ValueError(f"fanout metadata references unknown nodes: {sorted(fanout_missing)}")
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
        return Path(self.working_dir).expanduser().resolve()


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
