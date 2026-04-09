from __future__ import annotations

from pathlib import Path

from agentflow import skill_roots
from agentflow.specs import TargetSkillPolicyMode
from agentflow.skill_package_discovery import discover_skill_package
from agentflow.skill_package_models import DiscoveredSkillPackage, DiscoveredSkillWorkflow
from agentflow.skill_runtime_models import RenderedSkillPayload, ResolvedSkillPackage, ResolvedSkillWorkflow


def vendored_skill_packages_root() -> Path:
    return skill_roots.vendored_skill_packages_root()


def is_package_skill_ref(skill_ref: str) -> bool:
    return "::" in skill_ref or skill_ref.startswith("trailofbits/")


def resolve_skill_runtime(
    skill_ref: str,
    *,
    repo_root: Path | None = None,
    vendored_root: Path | None = None,
    package_roots: tuple[Path, ...] | None = None,
    target_skill_policy: TargetSkillPolicyMode | str = TargetSkillPolicyMode.NONE,
) -> ResolvedSkillPackage:
    package_locator, separator, workflow_ref = skill_ref.partition("::")
    if separator and not workflow_ref:
        raise ValueError(f"invalid skill package reference '{skill_ref}'; expected <package>::<workflow>")

    package, requested_workflow = _resolve_package_lookup(
        package_locator,
        workflow_ref=workflow_ref if separator else None,
        skill_ref=skill_ref,
        repo_root=repo_root,
        vendored_root=vendored_root,
        package_roots=package_roots,
        target_skill_policy=target_skill_policy,
    )
    workflow = _select_workflow(package, requested_workflow)
    scoped_assets = _scope_assets(package, workflow.workflow_id, workflow.skill_path)
    return ResolvedSkillPackage(
        skill_ref=skill_ref,
        package=package,
        package_name=package.package_name,
        source_kind=package.source_kind,
        package_root=package.package_root,
        workflow=ResolvedSkillWorkflow(workflow_id=workflow.workflow_id, skill_path=workflow.skill_path),
        workflow_layout=package.workflow_layout,
        top_level_skill_path=package.top_level_skill_path,
        workflow_ids=package.workflow_ids,
        reference_paths=scoped_assets["reference_paths"],
        resource_paths=scoped_assets["resource_paths"],
        command_paths=scoped_assets["command_paths"],
        agent_paths=scoped_assets["agent_paths"],
        script_paths=scoped_assets["script_paths"],
    )


def render_skill_payload(resolved: ResolvedSkillPackage) -> RenderedSkillPayload:
    label = "Skill package"
    sections = [
        f"{label} `{resolved.skill_ref}` from {resolved.workflow_skill_path}:",
        resolved.workflow_skill_path.read_text(encoding="utf-8").strip(),
    ]
    for asset_path in resolved.asset_paths:
        sections.append(f"Supporting asset from {asset_path}:\n{asset_path.read_text(encoding='utf-8').strip()}")
    return RenderedSkillPayload(
        resolved=resolved,
        label=label,
        sections=tuple(sections),
    )


def compile_rich_skill_prelude(
    skills: list[str],
    *,
    repo_root: Path | None = None,
    vendored_root: Path | None = None,
    package_roots: tuple[Path, ...] | None = None,
    target_skill_policy: TargetSkillPolicyMode | str = TargetSkillPolicyMode.NONE,
) -> str:
    sections: list[str] = []
    for skill_ref in skills:
        if not is_package_skill_ref(skill_ref):
            continue
        rendered = render_skill_payload(
            resolve_skill_runtime(
                skill_ref,
                repo_root=repo_root,
                vendored_root=vendored_root,
                package_roots=package_roots,
                target_skill_policy=target_skill_policy,
            )
        )
        sections.append(rendered.content)
    return "\n\n".join(sections)


def _resolve_package_lookup(
    package_locator: str,
    *,
    workflow_ref: str | None,
    skill_ref: str,
    repo_root: Path | None,
    vendored_root: Path | None,
    package_roots: tuple[Path, ...] | None,
    target_skill_policy: TargetSkillPolicyMode | str,
) -> tuple[DiscoveredSkillPackage, str | None]:
    if package_locator.startswith("trailofbits/"):
        _, _, package_name = package_locator.partition("/")
        if not package_name:
            raise ValueError(f"invalid skill package reference '{skill_ref}'; expected trailofbits/<package>")
        return _discover_vendored_package(package_name, vendored_root=vendored_root), workflow_ref

    if workflow_ref is None:
        raise ValueError(f"unsupported skill package reference '{skill_ref}'")

    return _discover_owned_package(
        package_locator,
        repo_root=repo_root,
        package_roots=package_roots,
        target_skill_policy=target_skill_policy,
    ), workflow_ref


def _discover_owned_package(
    package_name: str,
    *,
    repo_root: Path | None,
    package_roots: tuple[Path, ...] | None,
    target_skill_policy: TargetSkillPolicyMode | str,
) -> DiscoveredSkillPackage:
    roots = _normalize_package_roots(
        package_roots,
        repo_root=repo_root,
        target_skill_policy=target_skill_policy,
    )
    for root in roots:
        package_root = root / package_name
        if not package_root.exists():
            continue
        package = discover_skill_package(package_root)
        if package is None:
            raise ValueError(f"malformed skill package '{package_name}' at {package_root}")
        return package
    searched_roots = ", ".join(str(Path(root).expanduser().resolve()) for root in roots) or "<none>"
    raise ValueError(f"skill package '{package_name}' not found in configured skill roots: {searched_roots}")


def _normalize_package_roots(
    package_roots: tuple[Path, ...] | None,
    *,
    repo_root: Path | None,
    target_skill_policy: TargetSkillPolicyMode | str,
) -> tuple[Path, ...]:
    roots = tuple(Path(root) for root in skill_roots.owned_skill_package_roots()) if package_roots is None else tuple(
        Path(root) for root in package_roots
    )
    if _normalize_target_skill_policy(target_skill_policy) != TargetSkillPolicyMode.INHERIT_ALL:
        return roots

    combined = list(roots)
    seen = set(combined)
    for root in skill_roots.target_repo_skill_package_roots(repo_root):
        if root in seen:
            continue
        seen.add(root)
        combined.append(root)
    return tuple(combined)


def _normalize_target_skill_policy(target_skill_policy: TargetSkillPolicyMode | str) -> TargetSkillPolicyMode:
    if isinstance(target_skill_policy, TargetSkillPolicyMode):
        return target_skill_policy
    return TargetSkillPolicyMode(target_skill_policy)


def _discover_vendored_package(
    package_name: str,
    *,
    vendored_root: Path | None,
) -> DiscoveredSkillPackage:
    root = vendored_skill_packages_root() if vendored_root is None else Path(vendored_root)
    package_root = root / "trailofbits" / package_name
    if not package_root.exists():
        raise ValueError(f"skill package '{package_name}' not found in vendored trailofbits packages")
    package = discover_skill_package(package_root, source_kind="vendored")
    if package is None:
        raise ValueError(f"malformed vendored skill package '{package_name}' at {package_root}")
    return package


def _select_workflow(
    package: DiscoveredSkillPackage,
    workflow_ref: str | None,
) -> DiscoveredSkillWorkflow:
    if workflow_ref in (None, "default"):
        return _select_default_workflow(package)

    for workflow in package.workflows:
        if workflow.workflow_id == workflow_ref:
            return workflow

    raise ValueError(f"skill workflow '{workflow_ref}' not found in package '{package.package_name}'")


def _select_default_workflow(package: DiscoveredSkillPackage) -> DiscoveredSkillWorkflow:
    if package.top_level_skill_path is not None:
        return DiscoveredSkillWorkflow(workflow_id="default", skill_path=package.top_level_skill_path)
    if len(package.workflows) == 1:
        workflow = package.workflows[0]
        return DiscoveredSkillWorkflow(workflow_id="default", skill_path=workflow.skill_path)
    available_workflows = ", ".join(package.workflow_ids)
    raise ValueError(
        f"skill package '{package.package_name}' has no default workflow; use one of: {available_workflows}"
    )


def _scope_assets(
    package: DiscoveredSkillPackage,
    workflow_id: str,
    workflow_skill_path: Path,
) -> dict[str, tuple[Path, ...]]:
    return {
        "reference_paths": _scope_asset_bucket(package.reference_paths, package, workflow_id, workflow_skill_path),
        "resource_paths": _scope_asset_bucket(package.resource_paths, package, workflow_id, workflow_skill_path),
        "command_paths": _scope_asset_bucket(package.command_paths, package, workflow_id, workflow_skill_path),
        "agent_paths": _scope_asset_bucket(package.agent_paths, package, workflow_id, workflow_skill_path),
        "script_paths": _scope_asset_bucket(package.script_paths, package, workflow_id, workflow_skill_path),
    }


def _scope_asset_bucket(
    asset_paths: tuple[Path, ...],
    package: DiscoveredSkillPackage,
    workflow_id: str,
    workflow_skill_path: Path,
) -> tuple[Path, ...]:
    if package.workflow_layout != "nested":
        return asset_paths

    workflow_root = workflow_skill_path.parent
    include_workflow_assets = workflow_id != "default" or workflow_skill_path != package.top_level_skill_path
    scoped: list[Path] = []
    for asset_path in asset_paths:
        if _is_package_root_asset(asset_path, package.package_root):
            scoped.append(asset_path)
            continue
        if include_workflow_assets and _is_within(asset_path, workflow_root):
            scoped.append(asset_path)
    return tuple(scoped)


def _is_package_root_asset(asset_path: Path, package_root: Path) -> bool:
    try:
        relative = asset_path.relative_to(package_root)
    except ValueError:
        return False
    parts = relative.parts
    return len(parts) >= 2 and parts[0] in {"references", "resources", "commands", "agents", "scripts"}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
