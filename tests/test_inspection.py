from __future__ import annotations

import json
from pathlib import Path

from agentflow.inspection import build_launch_inspection, build_launch_inspection_summary, render_launch_inspection_summary
from agentflow.loader import load_pipeline_from_data
from agentflow.loader import load_pipeline_from_path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_build_launch_inspection_summary_keeps_ambient_base_url_inheritance_when_startup_does_not_export_it(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("export PATH=\"$PATH\"\n", encoding="utf-8")

    pipeline_path = tmp_path / "pipeline.json"
    pipeline_path.write_text(
        json.dumps({
            "name": "inspect-ambient-base-url",
            "working_dir": ".",
            "nodes": [
                {
                    "id": "plan",
                    "agent": "codex",
                    "prompt": "hi",
                    "target": {
                        "kind": "local",
                        "shell": "bash",
                        "shell_interactive": True,
                    },
                }
            ],
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example/v1")

    pipeline = load_pipeline_from_path(pipeline_path)
    report = build_launch_inspection(pipeline, runs_dir=str(tmp_path / ".agentflow"))
    summary = build_launch_inspection_summary(report)

    assert summary["nodes"][0]["launch_env_inheritances"] == [
        {
            "key": "OPENAI_BASE_URL",
            "current_value": "https://relay.example/v1",
            "source": "current environment",
        }
    ]
    assert summary["nodes"][0]["warnings"] == [
        "Launch inherits current `OPENAI_BASE_URL` value `https://relay.example/v1`; configure `provider` or "
        "`node.env` explicitly if you want Codex routing pinned for this node."
    ]


def test_build_launch_inspection_summary_reports_effective_bootstrap_home_when_target_overrides_home(
    tmp_path,
    monkeypatch,
):
    process_home = tmp_path / "process-home"
    process_home.mkdir()
    custom_home = tmp_path / "custom-home"
    custom_home.mkdir()
    (custom_home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (custom_home / ".bashrc").write_text("export PATH=\"$PATH\"\n", encoding="utf-8")

    pipeline_path = tmp_path / "pipeline.json"
    pipeline_path.write_text(
        json.dumps({
            "name": "inspect-custom-home",
            "working_dir": ".",
            "nodes": [
                {
                    "id": "plan",
                    "agent": "codex",
                    "prompt": "hi",
                    "target": {
                        "kind": "local",
                        "shell": f"env HOME={custom_home} bash",
                        "shell_login": True,
                        "shell_interactive": True,
                    },
                }
            ],
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(process_home))

    pipeline = load_pipeline_from_path(pipeline_path)
    report = build_launch_inspection(pipeline, runs_dir=str(tmp_path / ".agentflow"))
    summary = build_launch_inspection_summary(report)

    assert summary["nodes"][0]["bootstrap"] == (
        f"shell=env HOME={custom_home} bash, login=true, startup=~/.profile -> ~/.bashrc, interactive=true"
    )
    assert summary["nodes"][0]["bootstrap_home"] == str(custom_home.resolve())
    assert summary["nodes"][0]["bash_startup_files"] == {
        "~/.bash_profile": "missing",
        "~/.bash_login": "missing",
        "~/.profile": "present",
    }
    assert f"Bootstrap home: {custom_home.resolve()}" in render_launch_inspection_summary(report)
    assert (
        "Startup files: ~/.bash_profile=missing, ~/.bash_login=missing, ~/.profile=present"
        in render_launch_inspection_summary(report)
    )


def test_render_launch_inspection_summary_uses_notes_for_expected_env_pinning(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi() { export ANTHROPIC_API_KEY=test-kimi-key; }\n", encoding="utf-8")

    pipeline_path = tmp_path / "pipeline.json"
    pipeline_path.write_text(
        json.dumps({
            "name": "inspect-notes",
            "working_dir": ".",
            "nodes": [
                {
                    "id": "review",
                    "agent": "claude",
                    "provider": "kimi",
                    "prompt": "hi",
                    "target": {
                        "kind": "local",
                        "shell": "bash",
                        "shell_login": True,
                        "shell_interactive": True,
                        "shell_init": "kimi",
                    },
                }
            ],
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "current-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic")

    pipeline = load_pipeline_from_path(pipeline_path)
    report = build_launch_inspection(pipeline, runs_dir=str(tmp_path / ".agentflow"))
    rendered = render_launch_inspection_summary(report)
    summary = build_launch_inspection_summary(report)

    assert summary["nodes"][0]["notes"] == [
        "Launch env overrides current `ANTHROPIC_BASE_URL` from `https://open.bigmodel.cn/api/anthropic` to `https://api.kimi.com/coding/` via `provider.base_url`.",
        "Local shell bootstrap overrides current `ANTHROPIC_API_KEY` for this node via `target.shell_init` (`kimi` helper).",
    ]
    assert "Note: Launch env overrides current `ANTHROPIC_BASE_URL`" in rendered
    assert "Note: Local shell bootstrap overrides current `ANTHROPIC_API_KEY`" in rendered
    assert "Warning: Launch env overrides current `ANTHROPIC_BASE_URL`" not in rendered


def test_build_launch_inspection_summary_resolves_indirect_bootstrap_home_and_shell_auth(
    tmp_path,
    monkeypatch,
):
    process_home = tmp_path / "process-home"
    process_home.mkdir()
    custom_home = tmp_path / "custom-home"
    custom_home.mkdir()
    (custom_home / "auth.env").write_text("export ANTHROPIC_API_KEY=test-shell-key\n", encoding="utf-8")

    pipeline_path = tmp_path / "pipeline.json"
    pipeline_path.write_text(
        json.dumps({
            "name": "inspect-indirect-custom-home",
            "working_dir": ".",
            "nodes": [
                {
                    "id": "review",
                    "agent": "claude",
                    "prompt": "hi",
                    "target": {
                        "kind": "local",
                        "shell": f"env CUSTOM_HOME={custom_home} HOME=$CUSTOM_HOME BASH_ENV=$HOME/auth.env bash -c '{{command}}'",
                    },
                }
            ],
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(process_home))

    pipeline = load_pipeline_from_path(pipeline_path)
    report = build_launch_inspection(pipeline, runs_dir=str(tmp_path / ".agentflow"))
    summary = build_launch_inspection_summary(report)

    assert summary["nodes"][0]["bootstrap_home"] == str(custom_home.resolve())
    assert summary["nodes"][0]["auth"] == "`ANTHROPIC_API_KEY` via `target.shell`"
    assert f"Bootstrap home: {custom_home.resolve()}" in render_launch_inspection_summary(report)


def test_build_launch_inspection_summary_warns_when_active_login_startup_does_not_reach_bashrc(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text('export PATH="$HOME/bin:$PATH"\n', encoding="utf-8")
    (home / ".bashrc").write_text("export PATH=\"$HOME/.local/bin:$PATH\"\n", encoding="utf-8")

    pipeline_path = tmp_path / "pipeline.json"
    pipeline_path.write_text(
        json.dumps({
            "name": "inspect-startup-warning",
            "working_dir": ".",
            "nodes": [
                {
                    "id": "plan",
                    "agent": "codex",
                    "prompt": "hi",
                    "target": {
                        "kind": "local",
                        "shell": "bash",
                        "shell_login": True,
                        "shell_interactive": True,
                    },
                }
            ],
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    pipeline = load_pipeline_from_path(pipeline_path)
    report = build_launch_inspection(pipeline, runs_dir=str(tmp_path / ".agentflow"))
    summary = build_launch_inspection_summary(report)
    rendered = render_launch_inspection_summary(report)

    assert summary["nodes"][0]["warnings"] == [
        "Bash login startup uses `~/.bash_profile`, but it does not reach `~/.bashrc`."
    ]
    assert summary["nodes"][0]["shell_bridge"] == {
        "target": "~/.bash_profile",
        "source": "~/.bashrc",
        "reason": "Bash login shells use `~/.bash_profile`, but it does not reference `~/.bashrc`.",
        "snippet": 'if [ -f "$HOME/.bashrc" ]; then\n  . "$HOME/.bashrc"\nfi\n',
    }
    assert "Warning: Bash login startup uses `~/.bash_profile`, but it does not reach `~/.bashrc`." in rendered
    assert "Shell bridge suggestion for `~/.bash_profile` from `~/.bashrc`:" in rendered


def test_build_launch_inspection_summary_reports_skill_source_policy_and_resolved_packages(
    tmp_path,
    monkeypatch,
):
    repo_root = tmp_path / "target-repo"
    repo_root.mkdir()
    _write(repo_root / ".agents" / "skills" / "target-analysis" / "skills" / "review" / "SKILL.md", "# Target Review")

    owned_root = tmp_path / "agentflow-owned" / ".agents" / "skills"
    _write(owned_root / "owned-analysis" / "skills" / "plan" / "SKILL.md", "# Owned Plan")
    monkeypatch.setattr("agentflow.skill_roots.owned_skill_package_roots", lambda: (owned_root,))

    pipeline = load_pipeline_from_data(
        {
            "name": "inspect-skill-policy",
            "working_dir": str(repo_root),
            "nodes": [
                {
                    "id": "plan",
                    "agent": "codex",
                    "prompt": "Plan the work.",
                    "skills": ["owned-analysis::plan"],
                },
                {
                    "id": "review",
                    "agent": "claude",
                    "prompt": "Review the work.",
                    "skills": ["target-analysis::review"],
                    "target_skill_policy": "inherit_all",
                    "repo_instructions_mode": "ignore",
                },
            ],
        },
        base_dir=tmp_path,
    )

    report = build_launch_inspection(pipeline, runs_dir=str(tmp_path / ".agentflow"))
    summary = build_launch_inspection_summary(report)
    rendered = render_launch_inspection_summary(report)

    assert summary["nodes"][0]["target_skill_policy"] == "none"
    assert summary["nodes"][0]["skill_source_policy"] == (
        "AgentFlow-owned `.agents/skills/` roots only; target repo `.agents/skills/` are ignored by default."
    )
    assert summary["nodes"][0]["resolved_skills"] == [
        {
            "ref": "owned-analysis::plan",
            "kind": "package",
            "package": "owned-analysis",
            "workflow": "plan",
            "source": "agentflow_owned",
            "package_root": str((owned_root / "owned-analysis").resolve()),
            "workflow_skill_path": str((owned_root / "owned-analysis" / "skills" / "plan" / "SKILL.md").resolve()),
        }
    ]
    assert summary["nodes"][1]["repo_instructions_mode"] == "ignore"
    assert summary["nodes"][1]["target_skill_policy"] == "inherit_all"
    assert summary["nodes"][1]["skill_source_policy"] == (
        "AgentFlow-owned `.agents/skills/` roots remain authoritative; target repo `.agents/skills/` are trusted only when `target_skill_policy=inherit_all`."
    )
    assert summary["nodes"][1]["resolved_skills"] == [
        {
            "ref": "target-analysis::review",
            "kind": "package",
            "package": "target-analysis",
            "workflow": "review",
            "source": "target_repo",
            "package_root": str((repo_root / ".agents" / "skills" / "target-analysis").resolve()),
            "workflow_skill_path": str(
                (repo_root / ".agents" / "skills" / "target-analysis" / "skills" / "review" / "SKILL.md").resolve()
            ),
        }
    ]
    assert "Repo instructions: ignore" in rendered
    assert (
        "Skill source policy: AgentFlow-owned `.agents/skills/` roots only; target repo `.agents/skills/` are ignored by default."
        in rendered
    )
    assert "Resolved skill: owned-analysis::plan -> owned-analysis/plan from AgentFlow-owned `.agents/skills/`" in rendered
    assert (
        "Skill source policy: AgentFlow-owned `.agents/skills/` roots remain authoritative; target repo `.agents/skills/` are trusted only when `target_skill_policy=inherit_all`."
        in rendered
    )
    assert "Resolved skill: target-analysis::review -> target-analysis/review from target repo `.agents/skills/`" in rendered


def test_build_launch_inspection_prefers_local_trailofbits_directory_skill_over_vendored_classification(
    tmp_path: Path,
):
    repo_root = tmp_path / "target-repo"
    _write(repo_root / "trailofbits" / "release-skill" / "SKILL.md", "# Local Release Skill")

    pipeline = load_pipeline_from_data(
        {
            "name": "inspect-local-trailofbits-skill",
            "working_dir": str(repo_root),
            "nodes": [
                {
                    "id": "plan",
                    "agent": "codex",
                    "prompt": "Plan the work.",
                    "skills": ["trailofbits/release-skill"],
                }
            ],
        },
        base_dir=tmp_path,
    )

    report = build_launch_inspection(pipeline, runs_dir=str(tmp_path / ".agentflow"))
    summary = build_launch_inspection_summary(report)

    assert summary["nodes"][0]["resolved_skills"] == [
        {
            "ref": "trailofbits/release-skill",
            "kind": "local_path",
            "path": str((repo_root / "trailofbits" / "release-skill" / "SKILL.md").resolve()),
        }
    ]
