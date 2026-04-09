from __future__ import annotations

from pathlib import Path

import pytest

from agentflow.context import render_node_prompt
from agentflow.skills import compile_skill_prelude
from agentflow.specs import NodeResult, PipelineSpec


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _owned_package_roots(base: Path) -> tuple[Path, ...]:
    return (base / "agentflow-owned" / ".agents" / "skills",)


def _owned_package_root(base: Path) -> Path:
    return _owned_package_roots(base)[0]


def test_compile_skill_prelude_loads_relative_skill_directory(tmp_path: Path):
    skill_dir = tmp_path / "release-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("Follow the release checklist.", encoding="utf-8")

    prelude = compile_skill_prelude(["release-skill"], tmp_path)

    assert "Skill `release-skill`" in prelude
    assert "Follow the release checklist." in prelude


def test_compile_skill_prelude_loads_absolute_skill_directory(tmp_path: Path):
    skill_dir = tmp_path / "release-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("Follow the release checklist.", encoding="utf-8")

    prelude = compile_skill_prelude([str(skill_dir)], tmp_path)

    assert f"Skill `{skill_dir}`" in prelude
    assert "Follow the release checklist." in prelude


def test_compile_skill_prelude_loads_home_relative_skill_directory(
    tmp_path: Path,
    monkeypatch,
):
    home = tmp_path / "home"
    skill_dir = home / ".codex" / "skills" / "release-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Follow the shared release checklist.", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    prelude = compile_skill_prelude(["~/.codex/skills/release-skill"], tmp_path / "workspace")

    assert "Skill `~/.codex/skills/release-skill`" in prelude
    assert "Follow the shared release checklist." in prelude


def test_compile_skill_prelude_loads_owned_package_workflow_and_assets(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(package_root / "SKILL.md", "# Static Analysis Wrapper")
    _write(package_root / "skills" / "semgrep" / "SKILL.md", "# Semgrep Workflow")
    _write(package_root / "skills" / "semgrep" / "references" / "rules.md", "Use the curated semgrep rules.")
    _write(package_root / "resources" / "templates" / "config.yml", "severity: high")

    prelude = compile_skill_prelude(
        ["static-analysis::semgrep"],
        tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert "Skill package `static-analysis::semgrep`" in prelude
    assert "# Semgrep Workflow" in prelude
    assert "Use the curated semgrep rules." in prelude
    assert "severity: high" in prelude


def test_compile_skill_prelude_keeps_assets_for_single_nested_workflow_default(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "single"
    _write(package_root / "skills" / "only" / "SKILL.md", "# Only Workflow")
    _write(package_root / "skills" / "only" / "references" / "guide.md", "Use the only workflow guide.")
    _write(package_root / "skills" / "only" / "scripts" / "run.sh", "echo only")

    prelude = compile_skill_prelude(
        ["single::default"],
        tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert "Skill package `single::default`" in prelude
    assert "# Only Workflow" in prelude
    assert "Use the only workflow guide." in prelude
    assert "echo only" in prelude


def test_compile_skill_prelude_scopes_assets_to_selected_owned_workflow(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(package_root / "SKILL.md", "# Static Analysis Wrapper")
    _write(package_root / "references" / "shared.md", "Shared wrapper guidance.")
    _write(package_root / "skills" / "semgrep" / "SKILL.md", "# Semgrep Workflow")
    _write(package_root / "skills" / "semgrep" / "references" / "rules.md", "Use semgrep.")
    _write(package_root / "skills" / "codeql" / "SKILL.md", "# CodeQL Workflow")
    _write(package_root / "skills" / "codeql" / "references" / "queries.md", "Use codeql.")

    prelude = compile_skill_prelude(
        ["static-analysis::semgrep"],
        tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert "Shared wrapper guidance." in prelude
    assert "Use semgrep." in prelude
    assert "Use codeql." not in prelude


def test_compile_skill_prelude_ignores_target_working_dir_package_override(tmp_path: Path):
    owned_package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(owned_package_root / "SKILL.md", "# Owned Wrapper")
    _write(owned_package_root / "skills" / "semgrep" / "SKILL.md", "# Owned Semgrep")

    target_package_root = tmp_path / "target-repo" / ".agents" / "skills" / "static-analysis"
    _write(target_package_root / "SKILL.md", "# Target Wrapper")
    _write(target_package_root / "skills" / "semgrep" / "SKILL.md", "# Target Semgrep")

    prelude = compile_skill_prelude(
        ["static-analysis::semgrep"],
        tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert "# Owned Semgrep" in prelude
    assert "# Target Semgrep" not in prelude


def test_compile_skill_prelude_nested_working_dir_ignores_target_repo_packages(tmp_path: Path):
    owned_package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(owned_package_root / "SKILL.md", "# Owned Wrapper")
    _write(owned_package_root / "skills" / "semgrep" / "SKILL.md", "# Owned Semgrep")

    target_package_root = tmp_path / "target-repo" / ".agents" / "skills" / "static-analysis"
    _write(target_package_root / "SKILL.md", "# Target Wrapper")
    _write(target_package_root / "skills" / "semgrep" / "SKILL.md", "# Target Semgrep")

    prelude = compile_skill_prelude(
        ["static-analysis::semgrep"],
        tmp_path / "target-repo" / "apps" / "nested",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert "# Owned Semgrep" in prelude
    assert "# Target Semgrep" not in prelude


def test_compile_skill_prelude_allows_target_repo_package_when_trusted(tmp_path: Path):
    target_package_root = tmp_path / "target-repo" / ".agents" / "skills" / "static-analysis"
    _write(target_package_root / "SKILL.md", "# Target Wrapper")
    _write(target_package_root / "skills" / "semgrep" / "SKILL.md", "# Target Semgrep")

    prelude = compile_skill_prelude(
        ["static-analysis::semgrep"],
        tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
        target_skill_policy="inherit_all",
    )

    assert "# Target Semgrep" in prelude


def test_compile_skill_prelude_inherit_all_rejects_packages_above_target_repo_boundary(tmp_path: Path):
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
        compile_skill_prelude(
            ["static-analysis::semgrep"],
            nested_working_dir,
            package_roots=(),
            target_skill_policy="inherit_all",
        )


def test_compile_skill_prelude_target_skill_trust_does_not_affect_plain_local_paths(tmp_path: Path):
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "release-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Local release instructions.", encoding="utf-8")

    target_package_root = workspace / ".agents" / "skills" / "static-analysis"
    _write(target_package_root / "SKILL.md", "# Target Wrapper")
    _write(target_package_root / "skills" / "semgrep" / "SKILL.md", "# Target Semgrep")

    prelude = compile_skill_prelude(
        ["release-skill"],
        workspace,
        package_roots=_owned_package_roots(tmp_path),
        target_skill_policy="inherit_all",
    )

    assert "Skill `release-skill`" in prelude
    assert "Local release instructions." in prelude
    assert "# Target Semgrep" not in prelude


def test_compile_skill_prelude_loads_explicit_wrapper_default_for_owned_package(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "release"
    _write(package_root / "SKILL.md", "# Release Wrapper")
    _write(package_root / "skills" / "plan" / "SKILL.md", "# Release Plan")

    prelude = compile_skill_prelude(
        ["release::default"],
        tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert "Skill package `release::default`" in prelude
    assert "# Release Wrapper" in prelude
    assert "# Release Plan" not in prelude


def test_compile_skill_prelude_loads_vendored_compatibility_ref_with_default_wrapper(
    tmp_path: Path,
    monkeypatch,
):
    vendored_root = tmp_path / "security_skills"
    package_root = vendored_root / "trailofbits" / "building-secure-contracts"
    _write(package_root / "SKILL.md", "# Building Secure Contracts Wrapper")
    _write(package_root / "skills" / "audit-prep-assistant" / "SKILL.md", "# Audit Prep Assistant")

    monkeypatch.setattr("agentflow.skill_packages.vendored_skill_packages_root", lambda: vendored_root)

    prelude = compile_skill_prelude(["trailofbits/building-secure-contracts"], tmp_path / "workspace")

    assert "Skill package `trailofbits/building-secure-contracts`" in prelude
    assert "# Building Secure Contracts Wrapper" in prelude
    assert "# Audit Prep Assistant" not in prelude


def test_compile_skill_prelude_renders_repo_local_and_vendored_package_refs_from_same_runtime_contract(
    tmp_path: Path,
    monkeypatch,
):
    owned_package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(owned_package_root / "SKILL.md", "# Static Analysis Wrapper")
    _write(owned_package_root / "references" / "shared.md", "Shared wrapper guidance.")
    _write(owned_package_root / "skills" / "semgrep" / "SKILL.md", "# Semgrep Workflow")
    _write(owned_package_root / "skills" / "semgrep" / "references" / "rules.md", "Use semgrep.")

    vendored_root = tmp_path / "security_skills"
    vendored_package_root = vendored_root / "trailofbits" / "static-analysis"
    _write(vendored_package_root / "SKILL.md", "# Static Analysis Wrapper")
    _write(vendored_package_root / "references" / "shared.md", "Shared wrapper guidance.")
    _write(vendored_package_root / "skills" / "semgrep" / "SKILL.md", "# Semgrep Workflow")
    _write(vendored_package_root / "skills" / "semgrep" / "references" / "rules.md", "Use semgrep.")
    monkeypatch.setattr("agentflow.skill_packages.vendored_skill_packages_root", lambda: vendored_root)

    repo_local_prelude = compile_skill_prelude(
        ["static-analysis::semgrep"],
        tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )
    vendored_prelude = compile_skill_prelude(
        ["trailofbits/static-analysis::semgrep"],
        tmp_path / "target-repo",
        package_roots=_owned_package_roots(tmp_path),
    )

    assert "Skill package `static-analysis::semgrep`" in repo_local_prelude
    assert "Skill package `trailofbits/static-analysis::semgrep`" in vendored_prelude
    for expected in ("# Semgrep Workflow", "Shared wrapper guidance.", "Use semgrep."):
        assert expected in repo_local_prelude
        assert expected in vendored_prelude


def test_compile_skill_prelude_prefers_local_workspace_skill_when_path_starts_with_trailofbits(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "trailofbits" / "release-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Local trailofbits workspace skill.", encoding="utf-8")

    prelude = compile_skill_prelude(["trailofbits/release-skill"], workspace)

    assert "Skill `trailofbits/release-skill`" in prelude
    assert "Local trailofbits workspace skill." in prelude
    assert "Skill package `trailofbits/release-skill`" not in prelude


def test_compile_skill_prelude_raises_clear_error_for_missing_package(tmp_path: Path):
    with pytest.raises(ValueError, match="skill package 'missing-package' not found"):
        compile_skill_prelude(
            ["missing-package::plan"],
            tmp_path / "target-repo",
            package_roots=_owned_package_roots(tmp_path),
        )


def test_compile_skill_prelude_raises_clear_error_for_malformed_owned_package(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "broken-package"
    package_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="malformed skill package 'broken-package'"):
        compile_skill_prelude(
            ["broken-package::plan"],
            tmp_path / "target-repo",
            package_roots=_owned_package_roots(tmp_path),
        )


def test_compile_skill_prelude_raises_clear_error_for_malformed_vendored_package(
    tmp_path: Path,
    monkeypatch,
):
    vendored_root = tmp_path / "security_skills"
    package_root = vendored_root / "trailofbits" / "broken-package"
    package_root.mkdir(parents=True)
    monkeypatch.setattr("agentflow.skill_packages.vendored_skill_packages_root", lambda: vendored_root)

    with pytest.raises(ValueError, match="malformed vendored skill package 'broken-package'"):
        compile_skill_prelude(["trailofbits/broken-package::plan"], tmp_path)


def test_compile_skill_prelude_raises_clear_error_for_missing_workflow(tmp_path: Path):
    package_root = _owned_package_root(tmp_path) / "static-analysis"
    _write(package_root / "SKILL.md", "# Static Analysis Wrapper")
    _write(package_root / "skills" / "semgrep" / "SKILL.md", "# Semgrep Workflow")

    with pytest.raises(ValueError, match="skill workflow 'codeql' not found in package 'static-analysis'"):
        compile_skill_prelude(
            ["static-analysis::codeql"],
            tmp_path / "target-repo",
            package_roots=_owned_package_roots(tmp_path),
        )


def test_render_node_prompt_supports_directory_style_skill_paths(tmp_path: Path):
    skill_dir = tmp_path / "release-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("Check release notes.", encoding="utf-8")

    pipeline = PipelineSpec.model_validate(
        {
            "name": "skills-dir",
            "working_dir": str(tmp_path),
            "nodes": [
                {
                    "id": "plan",
                    "agent": "codex",
                    "prompt": "Summarize the repo.",
                    "skills": ["release-skill"],
                }
            ],
        }
    )

    prompt = render_node_prompt(pipeline, pipeline.nodes[0], {"plan": NodeResult(node_id="plan")})

    assert "Selected skills:" in prompt
    assert "Check release notes." in prompt
    assert prompt.endswith("Task:\nSummarize the repo.")


def test_render_node_prompt_supports_home_relative_skill_paths(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    skill_dir = home / ".codex" / "skills" / "review-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Review shared orchestration defaults.", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))

    pipeline = PipelineSpec.model_validate(
        {
            "name": "skills-home",
            "working_dir": str(workspace),
            "nodes": [
                {
                    "id": "plan",
                    "agent": "codex",
                    "prompt": "Summarize the repo.",
                    "skills": ["~/.codex/skills/review-skill"],
                }
            ],
        }
    )

    prompt = render_node_prompt(pipeline, pipeline.nodes[0], {"plan": NodeResult(node_id="plan")})

    assert "Selected skills:" in prompt
    assert "Review shared orchestration defaults." in prompt
    assert prompt.endswith("Task:\nSummarize the repo.")
