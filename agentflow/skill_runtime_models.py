from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentflow.skill_package_models import DiscoveredSkillPackage
from agentflow.skill_package_models import SkillPackageSourceKind, SkillPackageWorkflowLayout


@dataclass(frozen=True, slots=True)
class ResolvedSkillWorkflow:
    workflow_id: str
    skill_path: Path


@dataclass(frozen=True, slots=True)
class ResolvedSkillPackage:
    skill_ref: str
    package: DiscoveredSkillPackage
    package_name: str
    source_kind: SkillPackageSourceKind
    package_root: Path
    workflow: ResolvedSkillWorkflow
    workflow_layout: SkillPackageWorkflowLayout
    top_level_skill_path: Path | None = None
    workflow_ids: tuple[str, ...] = ()
    reference_paths: tuple[Path, ...] = ()
    resource_paths: tuple[Path, ...] = ()
    command_paths: tuple[Path, ...] = ()
    agent_paths: tuple[Path, ...] = ()
    script_paths: tuple[Path, ...] = ()

    @property
    def workflow_id(self) -> str:
        return self.workflow.workflow_id

    @property
    def workflow_skill_path(self) -> Path:
        return self.workflow.skill_path

    @property
    def asset_paths(self) -> tuple[Path, ...]:
        return (
            self.reference_paths
            + self.resource_paths
            + self.command_paths
            + self.agent_paths
            + self.script_paths
        )


@dataclass(frozen=True, slots=True)
class RenderedSkillPayload:
    resolved: ResolvedSkillPackage
    label: str
    sections: tuple[str, ...]

    @property
    def content(self) -> str:
        return "\n\n".join(self.sections)
