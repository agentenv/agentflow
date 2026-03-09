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
