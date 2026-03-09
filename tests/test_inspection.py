from __future__ import annotations

from agentflow.inspection import build_launch_inspection, build_launch_inspection_summary, render_launch_inspection_summary
from agentflow.loader import load_pipeline_from_path


def test_build_launch_inspection_summary_keeps_ambient_base_url_inheritance_when_startup_does_not_export_it(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("export PATH=\"$PATH\"\n", encoding="utf-8")

    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-ambient-base-url
working_dir: .
nodes:
  - id: plan
    agent: codex
    prompt: hi
    target:
      kind: local
      shell: bash
      shell_interactive: true
""",
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

    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        f"""name: inspect-custom-home
working_dir: .
nodes:
  - id: plan
    agent: codex
    prompt: hi
    target:
      kind: local
      shell: env HOME={custom_home} bash
      shell_login: true
      shell_interactive: true
""",
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

    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-notes
working_dir: .
nodes:
  - id: review
    agent: claude
    provider: kimi
    prompt: hi
    target:
      kind: local
      shell: bash
      shell_login: true
      shell_interactive: true
      shell_init: kimi
""",
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

    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        f"""name: inspect-indirect-custom-home
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: env CUSTOM_HOME={custom_home} HOME=$CUSTOM_HOME BASH_ENV=$HOME/auth.env bash -c '{{command}}'
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(process_home))

    pipeline = load_pipeline_from_path(pipeline_path)
    report = build_launch_inspection(pipeline, runs_dir=str(tmp_path / ".agentflow"))
    summary = build_launch_inspection_summary(report)

    assert summary["nodes"][0]["bootstrap_home"] == str(custom_home.resolve())
    assert summary["nodes"][0]["auth"] == "`ANTHROPIC_API_KEY` via `target.shell`"
    assert f"Bootstrap home: {custom_home.resolve()}" in render_launch_inspection_summary(report)
