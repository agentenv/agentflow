from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


SkillPackageSourceKind = Literal["repo_local", "vendored"]
SkillPackageWorkflowLayout = Literal["nested", "flat", "top_level_only"]


@dataclass(frozen=True, slots=True)
class SkillPackageAssets:
    reference_paths: tuple[Path, ...] = ()
    resource_paths: tuple[Path, ...] = ()
    command_paths: tuple[Path, ...] = ()
    agent_paths: tuple[Path, ...] = ()
    script_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveredSkillWorkflow:
    workflow_id: str
    skill_path: Path


@dataclass(frozen=True, slots=True)
class DiscoveredSkillPackage:
    package_name: str
    source_kind: SkillPackageSourceKind
    package_root: Path
    top_level_skill_path: Path | None
    workflow_layout: SkillPackageWorkflowLayout
    workflows: tuple[DiscoveredSkillWorkflow, ...]
    assets: SkillPackageAssets = SkillPackageAssets()

    @property
    def workflow_ids(self) -> tuple[str, ...]:
        return tuple(workflow.workflow_id for workflow in self.workflows)

    @property
    def workflow_skill_paths(self) -> tuple[Path, ...]:
        return tuple(workflow.skill_path for workflow in self.workflows)

    @property
    def reference_paths(self) -> tuple[Path, ...]:
        return self.assets.reference_paths

    @property
    def resource_paths(self) -> tuple[Path, ...]:
        return self.assets.resource_paths

    @property
    def command_paths(self) -> tuple[Path, ...]:
        return self.assets.command_paths

    @property
    def agent_paths(self) -> tuple[Path, ...]:
        return self.assets.agent_paths

    @property
    def script_paths(self) -> tuple[Path, ...]:
        return self.assets.script_paths
