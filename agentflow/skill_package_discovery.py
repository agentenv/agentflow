from __future__ import annotations

from pathlib import Path

from agentflow.skill_package_models import (
    DiscoveredSkillPackage,
    DiscoveredSkillWorkflow,
    SkillPackageAssets,
    SkillPackageSourceKind,
    SkillPackageWorkflowLayout,
)

_ASSET_BUCKETS = {
    "references": "reference_paths",
    "resources": "resource_paths",
    "commands": "command_paths",
    "agents": "agent_paths",
    "scripts": "script_paths",
}


def discover_skill_packages(repo_root: Path) -> list[DiscoveredSkillPackage]:
    skills_root = Path(repo_root) / ".agents" / "skills"
    if not skills_root.is_dir():
        return []

    packages: list[DiscoveredSkillPackage] = []
    for package_root in sorted((path for path in skills_root.iterdir() if path.is_dir()), key=lambda path: path.name):
        package = discover_skill_package(package_root)
        if package is not None:
            packages.append(package)
    return packages


def discover_skill_package(
    package_root: Path,
    *,
    source_kind: SkillPackageSourceKind = "repo_local",
) -> DiscoveredSkillPackage | None:
    root = Path(package_root)
    if not root.is_dir():
        return None

    top_level_skill_path = root / "SKILL.md"
    if not top_level_skill_path.is_file():
        top_level_skill_path = None

    workflow_layout, workflows = _discover_workflows(root, top_level_skill_path=top_level_skill_path)
    if not workflows:
        return None

    return DiscoveredSkillPackage(
        package_name=root.name,
        source_kind=source_kind,
        package_root=root,
        top_level_skill_path=top_level_skill_path,
        workflow_layout=workflow_layout,
        workflows=workflows,
        assets=_index_assets(root, workflow_layout=workflow_layout, workflows=workflows),
    )


def _discover_workflows(
    package_root: Path,
    *,
    top_level_skill_path: Path | None,
) -> tuple[SkillPackageWorkflowLayout | None, tuple[DiscoveredSkillWorkflow, ...]]:
    skills_root = package_root / "skills"
    nested_workflows: list[DiscoveredSkillWorkflow] = []

    if skills_root.is_dir():
        for workflow_root in sorted((path for path in skills_root.iterdir() if path.is_dir()), key=lambda path: path.name):
            workflow_skill_path = workflow_root / "SKILL.md"
            if workflow_skill_path.is_file():
                nested_workflows.append(
                    DiscoveredSkillWorkflow(workflow_id=workflow_root.name, skill_path=workflow_skill_path)
                )
        if nested_workflows:
            return "nested", tuple(nested_workflows)

        flat_skill_path = skills_root / "SKILL.md"
        if flat_skill_path.is_file():
            return "flat", (
                DiscoveredSkillWorkflow(workflow_id=package_root.name, skill_path=flat_skill_path),
            )

    if top_level_skill_path is not None:
        return "top_level_only", (
            DiscoveredSkillWorkflow(workflow_id=package_root.name, skill_path=top_level_skill_path),
        )

    return None, ()


def _index_assets(
    package_root: Path,
    *,
    workflow_layout: SkillPackageWorkflowLayout,
    workflows: tuple[DiscoveredSkillWorkflow, ...],
) -> SkillPackageAssets:
    indexed_paths = {field_name: set() for field_name in _ASSET_BUCKETS.values()}

    for asset_root in _intentional_asset_roots(package_root, workflow_layout=workflow_layout, workflows=workflows):
        for bucket_name, field_name in _ASSET_BUCKETS.items():
            bucket_root = asset_root / bucket_name
            if not bucket_root.is_dir():
                continue
            for child in bucket_root.rglob("*"):
                if child.is_file():
                    indexed_paths[field_name].add(child)

    return SkillPackageAssets(
        reference_paths=_sorted_paths(indexed_paths["reference_paths"]),
        resource_paths=_sorted_paths(indexed_paths["resource_paths"]),
        command_paths=_sorted_paths(indexed_paths["command_paths"]),
        agent_paths=_sorted_paths(indexed_paths["agent_paths"]),
        script_paths=_sorted_paths(indexed_paths["script_paths"]),
    )


def _sorted_paths(paths: set[Path]) -> tuple[Path, ...]:
    return tuple(sorted(paths, key=lambda path: path.as_posix()))


def _intentional_asset_roots(
    package_root: Path,
    *,
    workflow_layout: SkillPackageWorkflowLayout,
    workflows: tuple[DiscoveredSkillWorkflow, ...],
) -> tuple[Path, ...]:
    roots = {package_root}

    if workflow_layout == "flat":
        roots.add(package_root / "skills")
    elif workflow_layout == "nested":
        roots.update(workflow.skill_path.parent for workflow in workflows)

    return tuple(sorted(roots, key=lambda path: path.as_posix()))
