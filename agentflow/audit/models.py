from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LocalSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["local"]
    local_path: Path

    @field_validator("local_path")
    @classmethod
    def _normalize_local_path(cls, value: Path) -> Path:
        expanded = value.expanduser()
        if expanded.is_absolute():
            return expanded.resolve()
        return expanded


class GitHubSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["github"]
    repo_url: str
    commit: str

    @field_validator("commit")
    @classmethod
    def _validate_commit(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("commit must be a non-empty string")
        return normalized


class TargetReportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str
    audit_scope: str

    @field_validator("audit_scope")
    @classmethod
    def _validate_scope(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("audit_scope must be a non-empty report-safe relative path")
        posix_path = Path(normalized)
        windows_path = PureWindowsPath(normalized)
        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or any(part == ".." for part in posix_path.parts)
            or any(part == ".." for part in windows_path.parts)
        ):
            raise ValueError("audit_scope must be a non-empty report-safe relative path")
        return normalized


class ChainContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain: str | None = None
    contract_address_url: str | None = None
    creation_tx_url: str | None = None

    @field_validator("contract_address_url", "creation_tx_url")
    @classmethod
    def _validate_optional_urls(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("chain context URLs must use http or https")
        return normalized


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifacts_dir: str
    parallel_shards: int = Field(default=6, ge=1, le=32)

    @field_validator("artifacts_dir")
    @classmethod
    def _validate_artifacts_dir(cls, value: str) -> str:
        normalized = value.strip()
        posix_path = Path(normalized).expanduser()
        windows_path = PureWindowsPath(normalized)
        if not normalized:
            raise ValueError("artifacts_dir must be a non-empty path")
        if posix_path.is_absolute():
            return str(posix_path.resolve())
        if windows_path.is_absolute():
            return normalized
        if any(part == ".." for part in posix_path.parts) or any(part == ".." for part in windows_path.parts):
            raise ValueError("artifacts_dir must be a non-empty path")
        return normalized


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_source_confirmed_without_poc: bool = True
    max_poc_candidates: int = Field(default=5, ge=1, le=20)


class TargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: LocalSourceConfig | GitHubSourceConfig
    report: TargetReportConfig
    chain_context: ChainContext = Field(default_factory=ChainContext)


class ContractAuditManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: TargetConfig
    run: RunConfig
    policy: PolicyConfig = Field(default_factory=PolicyConfig)


class ReportManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str
    audit_scope: str
    source_mode: str
    source_identifier: str
    chain: str | None = None
    contract_address_url: str | None = None
    creation_tx_url: str | None = None


class ComponentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    symbol: str | None = None
    lines: tuple[int, int] | None = None


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    start_line: int
    end_line: int
    snippet_ref: str


class PocRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligible: bool = False
    status: Literal["not_attempted", "passed", "failed"] = "not_attempted"
    test_path: str | None = None


class ReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disposition: Literal["confirmed", "narrowed", "merged", "rejected", "requires_manual_poc_design"]
    notes: str = ""


class FindingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    severity: Literal["critical", "high", "medium", "low", "info"]
    category: str
    status: Literal["candidate", "validated", "final"] = "candidate"
    validation_status: Literal["poc_confirmed", "source_confirmed", "rejected"] = "source_confirmed"
    component: ComponentRef
    summary: str
    root_cause: str
    attack_scenario: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    poc: PocRecord = Field(default_factory=PocRecord)
    review: ReviewRecord
    dedup_fingerprint: str


class VerificationCommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "failed", "skipped"]
    command: str
    exit_code: int


class VerificationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    workspace: str
    build: VerificationCommandResult
    test: VerificationCommandResult
    stdout_log: str
    stderr_log: str
