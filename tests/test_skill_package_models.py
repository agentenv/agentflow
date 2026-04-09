from __future__ import annotations

from pathlib import Path

from agentflow.skill_package_models import (
    DiscoveredSkillPackage,
    DiscoveredSkillWorkflow,
    SkillPackageAssets,
)


def test_discovered_skill_package_exposes_derived_metadata() -> None:
    package_root = Path("/tmp/release-skill")
    top_level_skill = package_root / "SKILL.md"
    plan_skill = package_root / "skills" / "plan" / "SKILL.md"
    apply_skill = package_root / "skills" / "apply" / "SKILL.md"

    package = DiscoveredSkillPackage(
        package_name="release-skill",
        source_kind="repo_local",
        package_root=package_root,
        top_level_skill_path=top_level_skill,
        workflow_layout="nested",
        workflows=(
            DiscoveredSkillWorkflow(workflow_id="plan", skill_path=plan_skill),
            DiscoveredSkillWorkflow(workflow_id="apply", skill_path=apply_skill),
        ),
        assets=SkillPackageAssets(
            reference_paths=(package_root / "references" / "guide.md",),
            resource_paths=(package_root / "resources" / "templates" / "default.txt",),
            command_paths=(package_root / "commands" / "checks" / "run.md",),
            agent_paths=(package_root / "agents" / "reviewer.md",),
            script_paths=(package_root / "scripts" / "setup" / "bootstrap.sh",),
        ),
    )

    assert package.workflow_layout == "nested"
    assert package.workflow_ids == ("plan", "apply")
    assert package.workflow_skill_paths == (plan_skill, apply_skill)
    assert package.reference_paths == (package_root / "references" / "guide.md",)
    assert package.resource_paths == (package_root / "resources" / "templates" / "default.txt",)
    assert package.command_paths == (package_root / "commands" / "checks" / "run.md",)
    assert package.agent_paths == (package_root / "agents" / "reviewer.md",)
    assert package.script_paths == (package_root / "scripts" / "setup" / "bootstrap.sh",)
