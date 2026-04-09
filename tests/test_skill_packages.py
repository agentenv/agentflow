from __future__ import annotations

from pathlib import Path

import pytest

from agentflow.skill_packages import compile_rich_skill_prelude, resolve_skill_reference
from agentflow.skill_package_models import DiscoveredSkillPackage
from agentflow.skill_runtime import resolve_skill_runtime
from agentflow.skill_runtime_models import ResolvedSkillPackage


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _owned_package_roots(base: Path) -> tuple[Path, ...]:
    return (base / "agentflow-owned" / ".agents" / "skills",)


def _owned_package_root(base: Path) -> Path:
    return _owned_package_roots(base)[0]


def test_resolve_skill_reference_uses_owned_package_root_for_nested_workflow(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(package_root / "SKILL.md", "# Static Analysis Wrapper")
    _write(package_root / "skills" / "semgrep" / "SKILL.md", "# Semgrep Workflow")
    _write(package_root / "skills" / "semgrep" / "references" / "rules.md", "Use the curated semgrep rules.")

    resolved = resolve_skill_reference(
        "static-analysis::semgrep",
        repo_root=tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert resolved.package_name == "static-analysis"
    assert resolved.source_kind == "repo_local"
    assert resolved.workflow_id == "semgrep"
    assert resolved.package_root == package_root
    assert resolved.workflow_skill_path == package_root / "skills" / "semgrep" / "SKILL.md"
    assert resolved.reference_paths == (
        package_root / "skills" / "semgrep" / "references" / "rules.md",
    )


def test_resolve_skill_reference_supports_flat_workflow_layout(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "burpsuite-project-parser"
    _write(package_root / "skills" / "SKILL.md", "# Burp Workflow")

    resolved = resolve_skill_reference(
        "burpsuite-project-parser::burpsuite-project-parser",
        repo_root=tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert resolved.package_name == "burpsuite-project-parser"
    assert resolved.workflow_id == "burpsuite-project-parser"
    assert resolved.workflow_skill_path == package_root / "skills" / "SKILL.md"


def test_resolve_skill_reference_keeps_assets_for_single_nested_workflow_default(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "single"
    _write(package_root / "skills" / "only" / "SKILL.md", "# Only Workflow")
    _write(package_root / "skills" / "only" / "references" / "guide.md", "Use the only workflow guide.")
    _write(package_root / "skills" / "only" / "scripts" / "run.sh", "echo only")

    resolved = resolve_skill_reference(
        "single::default",
        repo_root=tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert resolved.workflow_id == "default"
    assert resolved.workflow_skill_path == package_root / "skills" / "only" / "SKILL.md"
    assert resolved.reference_paths == (
        package_root / "skills" / "only" / "references" / "guide.md",
    )
    assert resolved.script_paths == (
        package_root / "skills" / "only" / "scripts" / "run.sh",
    )


def test_resolve_skill_reference_supports_top_level_only_workflow(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "top-level-only"
    _write(package_root / "SKILL.md", "# Top Level Only")

    resolved = resolve_skill_reference(
        "top-level-only::top-level-only",
        repo_root=tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert resolved.package_name == "top-level-only"
    assert resolved.workflow_id == "top-level-only"
    assert resolved.workflow_skill_path == package_root / "SKILL.md"


def test_resolve_skill_reference_supports_explicit_default_wrapper_for_owned_package(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "release"
    _write(package_root / "SKILL.md", "# Release Wrapper")
    _write(package_root / "skills" / "plan" / "SKILL.md", "# Release Plan")

    resolved = resolve_skill_reference(
        "release::default",
        repo_root=tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert resolved.package_name == "release"
    assert resolved.workflow_id == "default"
    assert resolved.workflow_skill_path == package_root / "SKILL.md"


def test_resolve_skill_reference_does_not_satisfy_from_target_working_dir_package_root(tmp_path: Path):
    target_package_root = tmp_path / "target-repo" / ".agents" / "skills" / "static-analysis"
    _write(target_package_root / "SKILL.md", "# Target Wrapper")
    _write(target_package_root / "skills" / "semgrep" / "SKILL.md", "# Target Semgrep")

    with pytest.raises(ValueError, match="skill package 'static-analysis' not found"):
        resolve_skill_reference(
            "static-analysis::semgrep",
            repo_root=tmp_path / "target-repo",
            package_roots=_owned_package_roots(tmp_path),
        )


def test_resolve_skill_reference_nested_working_dir_still_uses_owned_package_root(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(package_root / "SKILL.md", "# Owned Wrapper")
    _write(package_root / "skills" / "semgrep" / "SKILL.md", "# Owned Semgrep")

    target_package_root = tmp_path / "target-repo" / ".agents" / "skills" / "static-analysis"
    _write(target_package_root / "SKILL.md", "# Target Wrapper")
    _write(target_package_root / "skills" / "semgrep" / "SKILL.md", "# Target Semgrep")

    resolved = resolve_skill_reference(
        "static-analysis::semgrep",
        repo_root=tmp_path / "target-repo" / "apps" / "nested",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert resolved.package_root == package_root
    assert resolved.workflow_skill_path.read_text(encoding="utf-8") == "# Owned Semgrep"


def test_resolve_skill_reference_allows_explicit_target_repo_package_root_when_trusted(tmp_path: Path):
    target_package_root = tmp_path / "target-repo" / ".agents" / "skills" / "static-analysis"
    _write(target_package_root / "SKILL.md", "# Target Wrapper")
    _write(target_package_root / "skills" / "semgrep" / "SKILL.md", "# Target Semgrep")

    resolved = resolve_skill_reference(
        "static-analysis::semgrep",
        repo_root=tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
        target_skill_policy="inherit_all",
    )

    assert resolved.package_root == target_package_root
    assert resolved.workflow_skill_path.read_text(encoding="utf-8") == "# Target Semgrep"


def test_resolve_skill_reference_inherit_all_rejects_packages_above_target_repo_boundary(tmp_path: Path):
    workspace = tmp_path / "workspace"
    repo_root = workspace / "target-repo"
    nested_working_dir = repo_root / "apps" / "nested"
    nested_working_dir.mkdir(parents=True)
    (workspace / ".git").mkdir()

    _write(repo_root / ".agents" / "skills" / "repo-only" / "skills" / "default" / "SKILL.md", "# Repo Only")

    ancestor_package_root = workspace / ".agents" / "skills" / "static-analysis"
    _write(ancestor_package_root / "SKILL.md", "# Ancestor Wrapper")
    _write(ancestor_package_root / "skills" / "semgrep" / "SKILL.md", "# Ancestor Semgrep")

    (repo_root / ".git").mkdir()

    with pytest.raises(ValueError, match="skill package 'static-analysis' not found"):
        resolve_skill_reference(
            "static-analysis::semgrep",
            repo_root=nested_working_dir,
            package_roots=(),
            target_skill_policy="inherit_all",
        )


def test_resolve_skill_reference_prefers_owned_package_root_over_trusted_target_repo_package(tmp_path: Path):
    owned_package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(owned_package_root / "SKILL.md", "# Owned Wrapper")
    _write(owned_package_root / "skills" / "semgrep" / "SKILL.md", "# Owned Semgrep")

    target_package_root = tmp_path / "target-repo" / ".agents" / "skills" / "static-analysis"
    _write(target_package_root / "SKILL.md", "# Target Wrapper")
    _write(target_package_root / "skills" / "semgrep" / "SKILL.md", "# Target Semgrep")

    resolved = resolve_skill_reference(
        "static-analysis::semgrep",
        repo_root=tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
        target_skill_policy="inherit_all",
    )

    assert resolved.package_root == owned_package_root
    assert resolved.workflow_skill_path.read_text(encoding="utf-8") == "# Owned Semgrep"


def test_resolve_skill_reference_supports_vendored_compatibility_refs(tmp_path: Path):
    vendored_root = tmp_path / "security_skills"
    package_root = vendored_root / "trailofbits" / "entry-point-analyzer"
    _write(package_root / "SKILL.md", "# Entry Point Analyzer Wrapper")
    _write(package_root / "skills" / "entry-point-analyzer" / "SKILL.md", "# Entry Point Analyzer")

    resolved = resolve_skill_reference(
        "trailofbits/entry-point-analyzer::entry-point-analyzer",
        repo_root=tmp_path,
        vendored_root=vendored_root,
    )

    assert resolved.package_name == "entry-point-analyzer"
    assert resolved.source_kind == "vendored"
    assert resolved.workflow_id == "entry-point-analyzer"
    assert resolved.workflow_skill_path == package_root / "skills" / "entry-point-analyzer" / "SKILL.md"


def test_resolve_skill_reference_supports_vendored_package_only_default_wrapper(tmp_path: Path):
    vendored_root = tmp_path / "security_skills"
    package_root = vendored_root / "trailofbits" / "building-secure-contracts"
    _write(package_root / "SKILL.md", "# Building Secure Contracts Wrapper")
    _write(package_root / "skills" / "audit-prep-assistant" / "SKILL.md", "# Audit Prep Assistant")

    resolved = resolve_skill_reference(
        "trailofbits/building-secure-contracts",
        repo_root=tmp_path,
        vendored_root=vendored_root,
    )

    assert resolved.package_name == "building-secure-contracts"
    assert resolved.source_kind == "vendored"
    assert resolved.workflow_id == "default"
    assert resolved.workflow_skill_path == package_root / "SKILL.md"


def test_resolve_skill_reference_owned_packages_do_not_require_default_vendored_root(
    tmp_path: Path,
    monkeypatch,
):
    package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(package_root / "SKILL.md", "# Static Analysis Wrapper")
    _write(package_root / "skills" / "semgrep" / "SKILL.md", "# Semgrep Workflow")

    def _missing_vendored_root() -> Path:
        raise FileNotFoundError("vendored skills disabled for this test")

    monkeypatch.setattr("agentflow.skill_packages.vendored_skill_packages_root", _missing_vendored_root)
    monkeypatch.setattr("agentflow.skill_runtime.vendored_skill_packages_root", _missing_vendored_root)

    resolved = resolve_skill_reference(
        "static-analysis::semgrep",
        repo_root=tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert resolved.package_root == package_root


def test_resolve_skill_reference_preserves_legacy_package_field_for_external_callers(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(package_root / "SKILL.md", "# Static Analysis Wrapper")
    _write(package_root / "references" / "shared.md", "Shared wrapper guidance.")
    _write(package_root / "skills" / "semgrep" / "SKILL.md", "# Semgrep Workflow")

    resolved = resolve_skill_reference(
        "static-analysis::semgrep",
        repo_root=tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert isinstance(resolved.package, DiscoveredSkillPackage)
    assert resolved.package.package_name == resolved.package_name == "static-analysis"
    assert resolved.package.source_kind == resolved.source_kind == "repo_local"
    assert resolved.package.package_root == resolved.package_root == package_root
    assert resolved.package.top_level_skill_path == resolved.top_level_skill_path == package_root / "SKILL.md"
    assert resolved.package.workflow_ids == resolved.workflow_ids == ("semgrep",)


def test_resolve_skill_runtime_returns_same_runtime_shape_for_repo_local_and_vendored_refs(tmp_path: Path):
    owned_package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(owned_package_root / "SKILL.md", "# Static Analysis Wrapper")
    _write(owned_package_root / "references" / "shared.md", "Shared wrapper guidance.")
    _write(owned_package_root / "skills" / "semgrep" / "SKILL.md", "# Semgrep Workflow")
    _write(owned_package_root / "skills" / "semgrep" / "references" / "rules.md", "Use semgrep.")
    _write(owned_package_root / "skills" / "semgrep" / "scripts" / "run.sh", "echo semgrep")

    vendored_root = tmp_path / "security_skills"
    vendored_package_root = vendored_root / "trailofbits" / "static-analysis"
    _write(vendored_package_root / "SKILL.md", "# Static Analysis Wrapper")
    _write(vendored_package_root / "references" / "shared.md", "Shared wrapper guidance.")
    _write(vendored_package_root / "skills" / "semgrep" / "SKILL.md", "# Semgrep Workflow")
    _write(vendored_package_root / "skills" / "semgrep" / "references" / "rules.md", "Use semgrep.")
    _write(vendored_package_root / "skills" / "semgrep" / "scripts" / "run.sh", "echo semgrep")

    repo_local = resolve_skill_runtime(
        "static-analysis::semgrep",
        repo_root=tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )
    vendored = resolve_skill_runtime(
        "trailofbits/static-analysis::semgrep",
        repo_root=tmp_path / "target-repo",
        vendored_root=vendored_root,
    )

    assert isinstance(repo_local, ResolvedSkillPackage)
    assert isinstance(vendored, ResolvedSkillPackage)
    assert type(repo_local) is type(vendored)
    assert repo_local.package_name == vendored.package_name == "static-analysis"
    assert repo_local.workflow_id == vendored.workflow_id == "semgrep"
    assert repo_local.workflow_skill_path.relative_to(repo_local.package_root) == vendored.workflow_skill_path.relative_to(
        vendored.package_root
    ) == Path("skills/semgrep/SKILL.md")
    assert tuple(path.relative_to(repo_local.package_root) for path in repo_local.reference_paths) == tuple(
        path.relative_to(vendored.package_root) for path in vendored.reference_paths
    ) == (
        Path("references/shared.md"),
        Path("skills/semgrep/references/rules.md"),
    )
    assert tuple(path.relative_to(repo_local.package_root) for path in repo_local.script_paths) == tuple(
        path.relative_to(vendored.package_root) for path in vendored.script_paths
    ) == (
        Path("skills/semgrep/scripts/run.sh"),
    )
    assert repo_local.source_kind == "repo_local"
    assert vendored.source_kind == "vendored"


def test_compile_rich_skill_prelude_includes_package_assets(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(package_root / "SKILL.md", "# Static Analysis Wrapper")
    _write(package_root / "skills" / "semgrep" / "SKILL.md", "# Semgrep Workflow")
    _write(package_root / "skills" / "semgrep" / "references" / "rules.md", "Use the curated semgrep rules.")
    _write(package_root / "scripts" / "bootstrap.sh", "echo bootstrap")

    prelude = compile_rich_skill_prelude(
        ["static-analysis::semgrep"],
        repo_root=tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert "Skill package `static-analysis::semgrep`" in prelude
    assert "# Semgrep Workflow" in prelude
    assert "Use the curated semgrep rules." in prelude
    assert "echo bootstrap" in prelude


def test_compile_rich_skill_prelude_scopes_nested_workflow_assets_to_selected_workflow(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(package_root / "SKILL.md", "# Static Analysis Wrapper")
    _write(package_root / "references" / "shared.md", "Shared wrapper guidance.")
    _write(package_root / "skills" / "semgrep" / "SKILL.md", "# Semgrep Workflow")
    _write(package_root / "skills" / "semgrep" / "references" / "rules.md", "Use semgrep.")
    _write(package_root / "skills" / "codeql" / "SKILL.md", "# CodeQL Workflow")
    _write(package_root / "skills" / "codeql" / "references" / "queries.md", "Use codeql.")

    prelude = compile_rich_skill_prelude(
        ["static-analysis::semgrep"],
        repo_root=tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert "Shared wrapper guidance." in prelude
    assert "Use semgrep." in prelude
    assert "Use codeql." not in prelude


def test_resolve_skill_reference_raises_clear_error_for_missing_package(tmp_path: Path):
    with pytest.raises(ValueError, match="skill package 'does-not-exist' not found"):
        resolve_skill_reference(
            "does-not-exist::semgrep",
            repo_root=tmp_path / "target-repo",
            package_roots=_owned_package_roots(tmp_path),
        )


def test_resolve_skill_reference_missing_package_error_lists_effective_search_roots_when_target_repo_is_trusted(
    tmp_path: Path,
):
    owned_root = _owned_package_root(tmp_path)
    repo_root = tmp_path / "target-repo"
    (repo_root / ".agents" / "skills").mkdir(parents=True)

    with pytest.raises(ValueError) as exc_info:
        resolve_skill_reference(
            "does-not-exist::semgrep",
            repo_root=repo_root,
            package_roots=(owned_root,),
            target_skill_policy="inherit_all",
        )

    message = str(exc_info.value)
    assert "configured skill roots" in message
    assert str(owned_root.resolve()) in message
    assert str((repo_root / ".agents" / "skills").resolve()) in message


def test_resolve_skill_reference_raises_clear_error_for_malformed_owned_package(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "broken-package"
    package_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="malformed skill package 'broken-package'"):
        resolve_skill_reference(
            "broken-package::semgrep",
            repo_root=tmp_path / "target-repo",
            package_roots=_owned_package_roots(tmp_path),
        )


def test_resolve_skill_reference_raises_clear_error_for_malformed_vendored_package(tmp_path: Path):
    vendored_root = tmp_path / "security_skills"
    package_root = vendored_root / "trailofbits" / "broken-package"
    package_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="malformed vendored skill package 'broken-package'"):
        resolve_skill_reference(
            "trailofbits/broken-package::semgrep",
            repo_root=tmp_path,
            vendored_root=vendored_root,
        )


def test_resolve_skill_reference_raises_clear_error_for_missing_workflow(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(package_root / "SKILL.md", "# Static Analysis Wrapper")
    _write(package_root / "skills" / "semgrep" / "SKILL.md", "# Semgrep Workflow")

    with pytest.raises(ValueError, match="skill workflow 'codeql' not found in package 'static-analysis'"):
        resolve_skill_reference(
            "static-analysis::codeql",
            repo_root=tmp_path / "target-repo",
            package_roots=_owned_package_roots(tmp_path),
        )
