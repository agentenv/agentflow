from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentflow import skill_roots
from agentflow.specs import TargetSkillPolicyMode
from agentflow.skill_package_models import DiscoveredSkillPackage, SkillPackageSourceKind
from agentflow.skill_runtime import compile_rich_skill_prelude as _compile_rich_skill_prelude
from agentflow.skill_runtime import is_package_skill_ref, resolve_skill_runtime
from agentflow.skill_runtime_models import ResolvedSkillPackage


@dataclass(frozen=True, slots=True)
class ResolvedSkillReference:
    skill_ref: str
    package: DiscoveredSkillPackage
    workflow_id: str
    workflow_skill_path: Path
    reference_paths: tuple[Path, ...] = ()
    resource_paths: tuple[Path, ...] = ()
    command_paths: tuple[Path, ...] = ()
    agent_paths: tuple[Path, ...] = ()
    script_paths: tuple[Path, ...] = ()

    @classmethod
    def from_runtime(cls, resolved: ResolvedSkillPackage) -> ResolvedSkillReference:
        return cls(
            skill_ref=resolved.skill_ref,
            package=resolved.package,
            workflow_id=resolved.workflow_id,
            workflow_skill_path=resolved.workflow_skill_path,
            reference_paths=resolved.reference_paths,
            resource_paths=resolved.resource_paths,
            command_paths=resolved.command_paths,
            agent_paths=resolved.agent_paths,
            script_paths=resolved.script_paths,
        )

    @property
    def package_name(self) -> str:
        return self.package.package_name

    @property
    def source_kind(self) -> SkillPackageSourceKind:
        return self.package.source_kind

    @property
    def package_root(self) -> Path:
        return self.package.package_root

    @property
    def top_level_skill_path(self) -> Path | None:
        return self.package.top_level_skill_path

    @property
    def workflow_ids(self) -> tuple[str, ...]:
        return self.package.workflow_ids

    @property
    def asset_paths(self) -> tuple[Path, ...]:
        return (
            self.reference_paths
            + self.resource_paths
            + self.command_paths
            + self.agent_paths
            + self.script_paths
        )


def vendored_skill_packages_root() -> Path:
    return skill_roots.vendored_skill_packages_root()


def resolve_skill_reference(
    skill_ref: str,
    *,
    repo_root: Path | None = None,
    vendored_root: Path | None = None,
    package_roots: tuple[Path, ...] | None = None,
    target_skill_policy: TargetSkillPolicyMode | str = TargetSkillPolicyMode.NONE,
) -> ResolvedSkillReference:
    resolved_vendored_root = (
        vendored_skill_packages_root()
        if vendored_root is None and skill_ref.startswith("trailofbits/")
        else vendored_root
    )
    return ResolvedSkillReference.from_runtime(
        resolve_skill_runtime(
            skill_ref,
            repo_root=repo_root,
            vendored_root=resolved_vendored_root,
            package_roots=package_roots,
            target_skill_policy=target_skill_policy,
        )
    )


def compile_rich_skill_prelude(
    skills: list[str],
    *,
    repo_root: Path | None = None,
    vendored_root: Path | None = None,
    package_roots: tuple[Path, ...] | None = None,
    target_skill_policy: TargetSkillPolicyMode | str = TargetSkillPolicyMode.NONE,
) -> str:
    resolved_vendored_root = (
        vendored_skill_packages_root()
        if vendored_root is None and any(skill.startswith("trailofbits/") for skill in skills)
        else vendored_root
    )
    return _compile_rich_skill_prelude(
        skills,
        repo_root=repo_root,
        vendored_root=resolved_vendored_root,
        package_roots=package_roots,
        target_skill_policy=target_skill_policy,
    )
