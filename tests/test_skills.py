from __future__ import annotations

from pathlib import Path

from agentflow.context import render_node_prompt
from agentflow.skills import compile_skill_prelude
from agentflow.specs import NodeResult, PipelineSpec


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
