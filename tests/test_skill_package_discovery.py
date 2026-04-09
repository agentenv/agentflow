from __future__ import annotations

from pathlib import Path

from agentflow.skill_package_discovery import discover_skill_package, discover_skill_packages


def _write(path: Path, content: str = "payload") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_discover_skill_package_supports_wrapper_with_nested_workflows(tmp_path: Path) -> None:
    package_root = tmp_path / ".agents" / "skills" / "release"
    _write(package_root / "SKILL.md", "# Release")
    _write(package_root / "skills" / "plan" / "SKILL.md", "# Plan")
    _write(package_root / "skills" / "apply" / "SKILL.md", "# Apply")

    package = discover_skill_package(package_root)

    assert package is not None
    assert package.package_name == "release"
    assert package.source_kind == "repo_local"
    assert package.package_root == package_root
    assert package.top_level_skill_path == package_root / "SKILL.md"
    assert package.workflow_layout == "nested"
    assert package.workflow_ids == ("apply", "plan")
    assert package.workflow_skill_paths == (
        package_root / "skills" / "apply" / "SKILL.md",
        package_root / "skills" / "plan" / "SKILL.md",
    )


def test_discover_skill_package_supports_flat_workflow_layout(tmp_path: Path) -> None:
    package_root = tmp_path / ".agents" / "skills" / "release"
    _write(package_root / "skills" / "SKILL.md", "# Release workflow")

    package = discover_skill_package(package_root)

    assert package is not None
    assert package.package_name == "release"
    assert package.top_level_skill_path is None
    assert package.workflow_layout == "flat"
    assert package.workflow_ids == ("release",)
    assert package.workflow_skill_paths == (package_root / "skills" / "SKILL.md",)


def test_discover_skill_package_supports_top_level_only_workflow(tmp_path: Path) -> None:
    package_root = tmp_path / ".agents" / "skills" / "release"
    _write(package_root / "SKILL.md", "# Release workflow")

    package = discover_skill_package(package_root)

    assert package is not None
    assert package.package_name == "release"
    assert package.top_level_skill_path == package_root / "SKILL.md"
    assert package.workflow_layout == "top_level_only"
    assert package.workflow_ids == ("release",)
    assert package.workflow_skill_paths == (package_root / "SKILL.md",)


def test_discover_skill_package_indexes_only_intentional_asset_roots_recursively(tmp_path: Path) -> None:
    package_root = tmp_path / ".agents" / "skills" / "release"
    _write(package_root / "SKILL.md", "# Release workflow")
    _write(package_root / "skills" / "review" / "SKILL.md", "# Review")
    _write(package_root / "skills" / "review" / "references" / "checks" / "guide.md")
    _write(package_root / "resources" / "templates" / "default.txt")
    _write(package_root / "resources" / "references" / "nested.md")
    _write(package_root / "commands" / "checks" / "run.md")
    _write(package_root / "agents" / "review" / "agent.md")
    _write(package_root / "scripts" / "bootstrap.sh")
    _write(package_root / "tools" / "scripts" / "bootstrap.sh")
    _write(package_root / "agents" / "scripts" / "ignore.sh")

    package = discover_skill_package(package_root)

    assert package is not None
    assert package.workflow_layout == "nested"
    assert package.reference_paths == (
        package_root / "skills" / "review" / "references" / "checks" / "guide.md",
    )
    assert package.resource_paths == (
        package_root / "resources" / "references" / "nested.md",
        package_root / "resources" / "templates" / "default.txt",
    )
    assert package.command_paths == (
        package_root / "commands" / "checks" / "run.md",
    )
    assert package.agent_paths == (
        package_root / "agents" / "review" / "agent.md",
        package_root / "agents" / "scripts" / "ignore.sh",
    )
    assert package.script_paths == (
        package_root / "scripts" / "bootstrap.sh",
    )


def test_discover_skill_package_ignores_stray_skill_siblings_for_asset_indexing(tmp_path: Path) -> None:
    package_root = tmp_path / ".agents" / "skills" / "release"
    _write(package_root / "SKILL.md", "# Wrapper")
    _write(package_root / "skills" / "review" / "SKILL.md", "# Review")
    _write(package_root / "skills" / "review" / "scripts" / "run.sh")
    _write(package_root / "skills" / "misc" / "scripts" / "helper.sh")

    package = discover_skill_package(package_root)

    assert package is not None
    assert package.workflow_layout == "nested"
    assert package.workflow_ids == ("review",)
    assert package.script_paths == (
        package_root / "skills" / "review" / "scripts" / "run.sh",
    )


def test_discover_skill_package_prefers_nested_workflows_over_flat_skill_file(tmp_path: Path) -> None:
    package_root = tmp_path / ".agents" / "skills" / "release"
    _write(package_root / "SKILL.md", "# Wrapper")
    _write(package_root / "skills" / "SKILL.md", "# Flat workflow")
    _write(package_root / "skills" / "plan" / "SKILL.md", "# Plan")

    package = discover_skill_package(package_root)

    assert package is not None
    assert package.workflow_layout == "nested"
    assert package.workflow_ids == ("plan",)
    assert package.workflow_skill_paths == (
        package_root / "skills" / "plan" / "SKILL.md",
    )


def test_discover_skill_package_prefers_flat_workflow_over_top_level_skill(tmp_path: Path) -> None:
    package_root = tmp_path / ".agents" / "skills" / "release"
    _write(package_root / "SKILL.md", "# Wrapper")
    _write(package_root / "skills" / "SKILL.md", "# Flat workflow")

    package = discover_skill_package(package_root)

    assert package is not None
    assert package.top_level_skill_path == package_root / "SKILL.md"
    assert package.workflow_layout == "flat"
    assert package.workflow_ids == ("release",)
    assert package.workflow_skill_paths == (
        package_root / "skills" / "SKILL.md",
    )


def test_discover_skill_package_indexes_flat_workflow_assets_from_skills_root(tmp_path: Path) -> None:
    package_root = tmp_path / ".agents" / "skills" / "release"
    _write(package_root / "skills" / "SKILL.md", "# Flat workflow")
    _write(package_root / "skills" / "references" / "guide.md")
    _write(package_root / "skills" / "scripts" / "bootstrap.sh")

    package = discover_skill_package(package_root)

    assert package is not None
    assert package.workflow_layout == "flat"
    assert package.reference_paths == (
        package_root / "skills" / "references" / "guide.md",
    )
    assert package.script_paths == (
        package_root / "skills" / "scripts" / "bootstrap.sh",
    )


def test_discover_skill_package_ignores_incidental_bucket_names_inside_asset_trees(tmp_path: Path) -> None:
    package_root = tmp_path / ".agents" / "skills" / "release"
    _write(package_root / "SKILL.md", "# Release workflow")
    _write(package_root / "references" / "guides" / "root.md")
    _write(package_root / "references" / "scripts" / "nested.sh")
    _write(package_root / "resources" / "agents" / "nested.md")

    package = discover_skill_package(package_root)

    assert package is not None
    assert package.reference_paths == (
        package_root / "references" / "guides" / "root.md",
        package_root / "references" / "scripts" / "nested.sh",
    )
    assert package.resource_paths == (
        package_root / "resources" / "agents" / "nested.md",
    )
    assert package.agent_paths == ()
    assert package.script_paths == ()


def test_discover_skill_packages_scans_repo_local_agents_directory(tmp_path: Path) -> None:
    package_root = tmp_path / ".agents" / "skills" / "release"
    _write(package_root / "SKILL.md", "# Release workflow")
    _write(tmp_path / ".agents" / "skills" / "notes.txt", "ignore me")
    (tmp_path / ".agents" / "skills" / "empty-package").mkdir(parents=True)

    packages = discover_skill_packages(tmp_path)

    assert packages == [
        discover_skill_package(package_root),
    ]


def test_discover_skill_package_supports_vendored_source_kind(tmp_path: Path) -> None:
    package_root = tmp_path / "vendored" / "release"
    _write(package_root / "SKILL.md", "# Release workflow")

    package = discover_skill_package(package_root, source_kind="vendored")

    assert package is not None
    assert package.package_name == "release"
    assert package.source_kind == "vendored"
    assert package.workflow_ids == ("release",)
