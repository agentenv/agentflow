from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from typer.testing import CliRunner

import agentflow.cli
import agentflow.local_shell
from agentflow.cli import app
from agentflow.doctor import DoctorCheck, DoctorReport, ShellBridgeRecommendation
from agentflow.specs import ProviderConfig

runner = CliRunner()


def _capture_pipeline_loader(captured: dict[str, object], fake_pipeline: object):
    def _load(path: str):
        captured["loaded_path"] = path
        return fake_pipeline

    return _load


def _doctor_report(status: str = "ok", detail: str = "ready") -> DoctorReport:
    check_status = "failed" if status == "failed" else "ok"
    return DoctorReport(
        status=status,
        checks=[DoctorCheck(name="kimi_shell_helper", status=check_status, detail=detail)],
    )


def _completed_subprocess(returncode: int = 0, *, stdout: str = "", stderr: str = ""):
    def _run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=returncode, stdout=stdout, stderr=stderr)

    return _run


def _shell_bridge_recommendation() -> ShellBridgeRecommendation:
    return ShellBridgeRecommendation(
        target="~/.bash_profile",
        source="~/.profile",
        snippet='if [ -f "$HOME/.profile" ]; then\n  . "$HOME/.profile"\nfi\n',
        reason="Bash login shells use `~/.bash_profile`, so `~/.profile` never runs.",
    )


def _completed_run(
    run_id: str,
    *,
    pipeline_name: str = "demo",
    status: str = "completed",
    nodes: dict[str, object] | None = None,
    pipeline_nodes: list[object] | None = None,
):
    return SimpleNamespace(
        id=run_id,
        status=SimpleNamespace(value=status),
        pipeline=SimpleNamespace(name=pipeline_name, nodes=pipeline_nodes or []),
        started_at="2026-03-08T04:11:03+00:00",
        finished_at="2026-03-08T04:11:10+00:00",
        nodes=nodes or {},
        model_dump=lambda mode="json": {"id": run_id, "status": status},
    )


def test_validate_command_outputs_normalized_pipeline(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: cli
working_dir: .
nodes:
  - id: alpha
    agent: codex
    prompt: hi
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(pipeline_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["name"] == "cli"
    assert payload["nodes"][0]["id"] == "alpha"


def test_python_module_entrypoint_displays_help():
    repo_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [sys.executable, "-m", "agentflow", "--help"],
        capture_output=True,
        check=False,
        cwd=repo_root,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Usage:" in completed.stdout
    assert "validate" in completed.stdout
    assert "check-local" in completed.stdout
    assert "smoke" in completed.stdout


def test_validate_resolves_working_dir_relative_to_pipeline_file(tmp_path, monkeypatch):
    pipeline_dir = tmp_path / "pipelines"
    pipeline_dir.mkdir()
    workdir = pipeline_dir / "workspace"
    workdir.mkdir()
    task_dir = workdir / "task"
    task_dir.mkdir()
    pipeline_path = pipeline_dir / "pipeline.yaml"
    pipeline_path.write_text(
        """name: cli
working_dir: workspace
nodes:
  - id: alpha
    agent: codex
    prompt: hi
    target:
      kind: local
      cwd: task
""",
        encoding="utf-8",
    )
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    result = runner.invoke(app, ["validate", str(pipeline_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["working_dir"] == str(workdir.resolve())
    assert payload["nodes"][0]["target"]["cwd"] == str(task_dir.resolve())


def test_validate_applies_local_target_defaults_and_resolves_relative_cwd(tmp_path, monkeypatch):
    pipeline_dir = tmp_path / "pipelines"
    pipeline_dir.mkdir()
    workdir = pipeline_dir / "workspace"
    workdir.mkdir()
    default_task_dir = workdir / "shared-task"
    default_task_dir.mkdir()
    pipeline_path = pipeline_dir / "pipeline.yaml"
    pipeline_path.write_text(
        """name: cli-local-defaults
working_dir: workspace
local_target_defaults:
  shell: bash
  shell_login: true
  shell_interactive: true
  shell_init:
    - command -v kimi >/dev/null 2>&1
    - kimi
  cwd: shared-task
nodes:
  - id: alpha
    agent: codex
    prompt: hi
""",
        encoding="utf-8",
    )
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    result = runner.invoke(app, ["validate", str(pipeline_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["local_target_defaults"]["cwd"] == str(default_task_dir.resolve())
    assert payload["nodes"][0]["target"]["shell"] == "bash"
    assert payload["nodes"][0]["target"]["cwd"] == str(default_task_dir.resolve())


def test_validate_reports_pipeline_validation_error_without_traceback(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: cli-invalid-bash
working_dir: .
nodes:
  - id: alpha
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: bash --rcfile=\"$HOME/.bashrc\" -ic 'kimi && {command}'
      shell_init: kimi
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(pipeline_path)])

    assert result.exit_code == 1
    assert f"Failed to load pipeline `{pipeline_path}`:" in result.stderr
    assert "unsupported bash long option" in result.stderr
    assert "--rcfile=..." in result.stderr
    assert "Traceback" not in result.stderr


def test_inspect_command_outputs_launch_summary(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-demo
working_dir: .
nodes:
  - id: plan
    agent: codex
    model: gpt-5
    prompt: "Reply with exactly: codex ok"
    target:
      kind: local
      shell: bash
      shell_login: true
      shell_interactive: true
      shell_init: kimi

  - id: review
    agent: claude
    depends_on: [plan]
    prompt: |
      Review this: {{ nodes.plan.output }}
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = runner.invoke(app, ["inspect", str(pipeline_path)])

    assert result.exit_code == 0
    assert "Pipeline: inspect-demo" in result.stdout
    assert "Auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap." in result.stdout
    assert "Auto preflight matches: plan (codex) via `target.shell_init`" in result.stdout
    assert "Note: Dependency references use placeholder node outputs" in result.stdout
    assert "- plan [codex/local]" in result.stdout
    assert "Model: gpt-5" in result.stdout
    assert "Mode: tools=read_only, capture=final" in result.stdout
    assert "Bootstrap: shell=bash, login=true, interactive=true, init=kimi" in result.stdout
    assert "Launch: bash -l -i -c 'kimi && eval \"$AGENTFLOW_TARGET_COMMAND\"'" in result.stdout
    assert "Runtime files: codex_home/config.toml" in result.stdout
    assert "Prompt: Review this: <inspect placeholder for nodes.plan.output>" in result.stdout


def test_inspect_command_node_filter_omits_unrelated_placeholder_note(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-node-filter
working_dir: .
nodes:
  - id: plan
    agent: codex
    prompt: "Reply with exactly: codex ok"

  - id: review
    agent: claude
    depends_on: [plan]
    prompt: |
      Review this: {{ nodes.plan.output }}
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--node", "plan"])

    assert result.exit_code == 0
    assert "- plan [codex/local]" in result.stdout
    assert "- review [claude/local]" not in result.stdout
    assert "Note: Dependency references use placeholder node outputs" not in result.stdout


def test_inspect_command_omits_placeholder_note_for_plain_text_nodes_reference(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-plain-text-nodes
working_dir: .
nodes:
  - id: plan
    agent: codex
    prompt: |
      Explain why the string nodes.plan.output should stay literal in this doc example.
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path)])

    assert result.exit_code == 0
    assert "Prompt: Explain why the string nodes.plan.output should stay literal in this doc example." in result.stdout
    assert "Note: Dependency references use placeholder node outputs" not in result.stdout


def test_inspect_command_supports_json_output_and_redacts_env(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-json
working_dir: .
nodes:
  - id: review
    agent: claude
    provider: kimi
    prompt: "Reply with exactly: claude ok"
    target:
      kind: local
      shell: bash
      shell_login: true
      shell_interactive: true
      shell_init: kimi
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--node", "review", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"]["name"] == "inspect-json"
    assert payload["pipeline"]["auto_preflight"] == {
        "enabled": True,
        "reason": "local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.",
        "matches": [
            {
                "node_id": "review",
                "agent": "claude",
                "trigger": "target.shell_init",
            }
        ],
        "match_summary": ["review (claude) via `target.shell_init`"],
    }
    assert [node["id"] for node in payload["nodes"]] == ["review"]
    assert payload["nodes"][0]["tools"] == "read_only"
    assert payload["nodes"][0]["capture"] == "final"
    assert payload["nodes"][0]["resolved_provider"] == {
        "name": "kimi",
        "base_url": "https://api.kimi.com/coding/",
        "api_key_env": "ANTHROPIC_API_KEY",
        "wire_api": None,
        "headers": {},
        "env": {},
    }
    assert payload["nodes"][0]["prepared"]["env"]["ANTHROPIC_API_KEY"] == "<redacted>"
    assert payload["nodes"][0]["launch"]["env"]["ANTHROPIC_API_KEY"] == "<redacted>"
    assert payload["nodes"][0]["launch"]["env"]["ANTHROPIC_BASE_URL"] == "https://api.kimi.com/coding/"


def test_inspect_command_supports_json_summary_output(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-json-summary
working_dir: .
nodes:
  - id: review
    agent: claude
    provider: kimi
    prompt: "Reply with exactly: claude ok"
    target:
      kind: local
      shell: bash
      shell_login: true
      shell_interactive: true
      shell_init: kimi
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--node", "review", "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"] == {
        "name": "inspect-json-summary",
        "working_dir": str(tmp_path.resolve()),
        "node_count": 1,
        "auto_preflight": "enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.",
        "auto_preflight_matches": ["review (claude) via `target.shell_init`"],
    }
    assert payload["nodes"] == [
        {
            "id": "review",
            "agent": "claude",
            "target": "local",
            "tools": "read_only",
            "capture": "final",
            "provider": "kimi, key=ANTHROPIC_API_KEY, url=https://api.kimi.com/coding/",
            "auth": "`ANTHROPIC_API_KEY` via `target.shell_init` (`kimi` helper)",
            "bootstrap": "shell=bash, login=true, interactive=true, init=kimi",
            "prompt_preview": "Reply with exactly: claude ok",
            "prepared_command": "claude -p 'Reply with exactly: claude ok' --output-format stream-json --verbose --permission-mode bypassPermissions --tools Read,Glob,Grep,LS,NotebookRead,Task,TaskOutput,TodoRead,WebFetch,WebSearch",
            "launch": "bash -l -i -c 'kimi && eval \"$AGENTFLOW_TARGET_COMMAND\"'",
            "cwd": str(tmp_path.resolve()),
            "env_keys": ["AGENTFLOW_TARGET_COMMAND", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"],
        }
    ]


def test_inspect_command_summary_warns_when_launch_env_overrides_current_base_url(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-base-url-override
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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic")

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["launch_env_overrides"] == [
        {
            "key": "ANTHROPIC_BASE_URL",
            "current_value": "https://open.bigmodel.cn/api/anthropic",
            "launch_value": "https://api.kimi.com/coding/",
            "source": "provider.base_url",
        }
    ]
    assert payload["nodes"][0]["warnings"] == [
        "Launch env overrides current `ANTHROPIC_BASE_URL` from `https://open.bigmodel.cn/api/anthropic` to `https://api.kimi.com/coding/` via `provider.base_url`."
    ]


def test_inspect_command_summary_redacts_sensitive_launch_env_override_details(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-secret-override
working_dir: .
nodes:
  - id: plan
    agent: codex
    prompt: hi
    env:
      OPENAI_API_KEY: node-secret
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "current-secret")

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["launch_env_overrides"] == [
        {"key": "OPENAI_API_KEY", "redacted": True, "source": "node.env"}
    ]
    assert payload["nodes"][0]["warnings"] == [
        "Launch env overrides current `OPENAI_API_KEY` for this node via `node.env`."
    ]


def test_inspect_command_redacts_inline_shell_bootstrap_secrets(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-secret-shell-init
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
      shell_init:
        - export ANTHROPIC_API_KEY=super-secret-inline
        - kimi
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")

    json_result = runner.invoke(app, ["inspect", str(pipeline_path), "--node", "review", "--output", "json"])

    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert payload["nodes"][0]["target"]["shell_init"] == [
        "export ANTHROPIC_API_KEY=<redacted>",
        "kimi",
    ]
    assert payload["nodes"][0]["launch"]["command"] == [
        "bash",
        "-l",
        "-i",
        "-c",
        'export ANTHROPIC_API_KEY=<redacted> && kimi && eval "$AGENTFLOW_TARGET_COMMAND"',
    ]
    assert payload["nodes"][0]["launch"]["command_text"] == (
        "bash -l -i -c 'export ANTHROPIC_API_KEY=<redacted> && kimi && eval \"$AGENTFLOW_TARGET_COMMAND\"'"
    )

    summary_result = runner.invoke(app, ["inspect", str(pipeline_path), "--node", "review", "--output", "json-summary"])

    assert summary_result.exit_code == 0
    summary_payload = json.loads(summary_result.stdout)
    assert summary_payload["nodes"][0]["bootstrap"] == (
        "shell=bash, login=true, interactive=true, init=export ANTHROPIC_API_KEY=<redacted> && kimi"
    )
    assert summary_payload["nodes"][0]["auth"] == "`ANTHROPIC_API_KEY` via `target.shell_init`"
    assert summary_payload["nodes"][0]["launch"] == (
        "bash -l -i -c 'export ANTHROPIC_API_KEY=<redacted> && kimi && eval \"$AGENTFLOW_TARGET_COMMAND\"'"
    )


def test_inspect_command_summary_shows_codex_login_fallback(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-codex-auth
working_dir: .
nodes:
  - id: plan
    agent: codex
    prompt: hi
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["auth"] == "Codex CLI login or `OPENAI_API_KEY` via current environment"


def test_inspect_command_summary_mentions_codex_kimi_bootstrap_with_openai_env(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-codex-kimi-env
working_dir: .
nodes:
  - id: plan
    agent: codex
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
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret")

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["auth"] == (
        "`OPENAI_API_KEY` via current environment; `target.shell_init` (`kimi` helper) also runs before launch"
    )


def test_inspect_command_summary_mentions_codex_kimi_bootstrap_for_login_fallback(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-codex-kimi-login
working_dir: .
nodes:
  - id: plan
    agent: codex
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
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["auth"] == (
        "Codex CLI login via `target.shell_init` (`kimi` helper) or `OPENAI_API_KEY` via current environment"
    )


def test_inspect_command_summary_treats_shell_prefix_openai_key_as_auth_source(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-codex-shell-prefix-openai-key
working_dir: .
nodes:
  - id: plan
    agent: codex
    prompt: hi
    target:
      kind: local
      shell: env OPENAI_API_KEY=test-shell-key bash -c
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["auth"] == "`OPENAI_API_KEY` via `target.shell`"


def test_inspect_command_summary_treats_shell_prefix_provider_key_as_auth_source(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-claude-shell-prefix-provider-key
working_dir: .
nodes:
  - id: review
    agent: claude
    provider: anthropic
    prompt: hi
    target:
      kind: local
      shell: env ANTHROPIC_API_KEY=test-shell-key bash -c
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["auth"] == "`ANTHROPIC_API_KEY` via `target.shell`"


def test_inspect_command_summary_treats_custom_kimi_provider_shell_bootstrap_as_auth_source(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-custom-kimi-provider
working_dir: .
nodes:
  - id: review
    agent: claude
    provider:
      name: kimi-proxy
      base_url: https://api.kimi.com/coding/
      api_key_env: ANTHROPIC_API_KEY
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
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["auth"] == "`ANTHROPIC_API_KEY` via `target.shell_init` (`kimi` helper)"


def test_inspect_command_summary_treats_custom_kimi_provider_env_base_url_shell_bootstrap_as_auth_source(
    tmp_path,
    monkeypatch,
):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-custom-kimi-provider-env-base-url
working_dir: .
nodes:
  - id: review
    agent: claude
    provider:
      name: kimi-proxy
      api_key_env: ANTHROPIC_API_KEY
      env:
        ANTHROPIC_BASE_URL: https://api.kimi.com/coding/
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
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["auth"] == "`ANTHROPIC_API_KEY` via `target.shell_init` (`kimi` helper)"


def test_inspect_command_summary_prefers_kimi_helper_auth_over_node_env(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-helper-over-node-env
working_dir: .
nodes:
  - id: review
    agent: claude
    provider: kimi
    prompt: hi
    env:
      ANTHROPIC_API_KEY: node-secret
    target:
      kind: local
      shell: bash
      shell_login: true
      shell_interactive: true
      shell_init: kimi
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "current-secret")

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["auth"] == "`ANTHROPIC_API_KEY` via `target.shell_init` (`kimi` helper)"


def test_inspect_command_reports_disabled_auto_preflight_for_plain_pipeline(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-no-preflight
working_dir: .
nodes:
  - id: plan
    agent: codex
    prompt: "Reply with exactly: codex ok"
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"]["auto_preflight"] == (
        "disabled - path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap."
    )


def test_inspect_command_reports_auto_preflight_match_sources(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-preflight-matches
working_dir: .
nodes:
  - id: plan
    agent: codex
    prompt: hi
    target:
      kind: local
      shell: bash
      shell_login: true
      shell_interactive: true
      shell_init: kimi

  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: "bash -lic 'kimi && {command}'"
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"]["auto_preflight"]["matches"] == [
        {
            "node_id": "plan",
            "agent": "codex",
            "trigger": "target.shell_init",
        },
        {
            "node_id": "review",
            "agent": "claude",
            "trigger": "target.shell",
        },
    ]
    assert payload["pipeline"]["auto_preflight"]["match_summary"] == [
        "plan (codex) via `target.shell_init`",
        "review (claude) via `target.shell`",
    ]


def test_inspect_command_ignores_non_helper_kimi_substrings_in_auto_preflight(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-substrings
working_dir: .
nodes:
  - id: plan
    agent: codex
    prompt: hi
    target:
      kind: local
      shell: "bash -lc 'printf kimi-ready && {command}'"
      shell_init: echo kimi-ready
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"]["auto_preflight"] == {
        "enabled": False,
        "reason": "path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.",
        "matches": [],
        "match_summary": [],
    }


def test_inspect_command_ignores_plain_text_kimi_tokens_in_auto_preflight(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-plain-text
working_dir: .
nodes:
  - id: plan
    agent: codex
    prompt: hi
    target:
      kind: local
      shell: "bash -lc 'echo kimi && {command}'"
      shell_init: echo kimi
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"]["auto_preflight"] == {
        "enabled": False,
        "reason": "path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.",
        "matches": [],
        "match_summary": [],
    }


def test_inspect_command_supports_shell_init_command_lists(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-shell-init-list
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: bash
      shell_login: true
      shell_interactive: true
      shell_init:
        - command -v kimi >/dev/null 2>&1
        - kimi
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path)])

    assert result.exit_code == 0
    assert "Auto preflight matches: review (claude) via `target.shell_init`" in result.stdout
    assert "Bootstrap: shell=bash, login=true, interactive=true, init=command -v kimi >/dev/null 2>&1 && kimi" in result.stdout


def test_inspect_command_ignores_kimi_probe_commands_in_auto_preflight(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-probes
working_dir: .
nodes:
  - id: plan
    agent: codex
    prompt: hi
    target:
      kind: local
      shell: "bash -lc 'command -v kimi >/dev/null && {command}'"
      shell_init: type kimi >/dev/null 2>&1
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"]["auto_preflight"] == {
        "enabled": False,
        "reason": "path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.",
        "matches": [],
        "match_summary": [],
    }


def test_inspect_command_warns_when_kimi_shell_init_is_not_interactive(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-warning
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: bash
      shell_login: true
      shell_init: kimi
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path)])

    assert result.exit_code == 0
    assert "Warning: `shell_init: kimi` uses bash without interactive startup; helpers from `~/.bashrc` are usually unavailable." in result.stdout
    assert "Set `target.shell_interactive: true` or use `bash -lic`." in result.stdout


def test_inspect_command_accepts_interactive_bash_rcfile_wrapper(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-rcfile
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: "bash --rcfile ~/.bashrc -ic '{command}'"
      shell_init: kimi
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0].get("warnings") is None


def test_inspect_command_accepts_bash_env_kimi_wrapper(tmp_path):
    shell_env = tmp_path / "shell.env"
    shell_env.write_text("kimi(){ :; }\n", encoding="utf-8")

    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        f"""name: inspect-kimi-bash-env
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: \"env BASH_ENV={shell_env} bash -c 'kimi && {{command}}'\"
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"] == {
        "name": "inspect-kimi-bash-env",
        "working_dir": str(tmp_path.resolve()),
        "node_count": 1,
        "auto_preflight": "enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.",
        "auto_preflight_matches": ["review (claude) via `target.shell`"],
    }
    assert payload["nodes"][0].get("warnings") is None


def test_inspect_command_accepts_bash_env_wrapper_that_sources_helper_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".agentflow-kimi").write_text("kimi(){ :; }\n", encoding="utf-8")
    (home / "shell.env").write_text('source "$HOME/.agentflow-kimi"\n', encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-bash-env-source
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: "env BASH_ENV=$HOME/shell.env bash -c 'kimi && {command}'"
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"] == {
        "name": "inspect-kimi-bash-env-source",
        "working_dir": str(tmp_path.resolve()),
        "node_count": 1,
        "auto_preflight": "enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.",
        "auto_preflight_matches": ["review (claude) via `target.shell`"],
    }
    assert payload["nodes"][0].get("warnings") is None


def test_inspect_command_json_summary_includes_kimi_shell_init_warning(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-warning-json
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: bash
      shell_login: true
      shell_init: kimi
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["warnings"] == [
        "`shell_init: kimi` uses bash without interactive startup; helpers from `~/.bashrc` are usually unavailable. Set `target.shell_interactive: true` or use `bash -lic`."
    ]


def test_inspect_command_warns_when_explicit_kimi_shell_wrapper_is_not_interactive(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-wrapper-warning
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: "bash -lc 'kimi && {command}'"
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path)])

    assert result.exit_code == 0
    assert "Warning: `target.shell` uses `kimi` with bash without interactive startup; helpers from `~/.bashrc` are usually unavailable." in result.stdout
    assert "Add `-i`, set `target.shell_interactive: true`, or use `bash -lic`." in result.stdout


def test_inspect_command_detects_eval_style_kimi_wrapper_in_auto_preflight(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-eval-wrapper
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: "bash -lc 'eval \\\"$(kimi)\\\" && {command}'"
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"]["auto_preflight"] == {
        "enabled": True,
        "reason": "local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.",
        "matches": [{"node_id": "review", "agent": "claude", "trigger": "target.shell"}],
        "match_summary": ["review (claude) via `target.shell`"],
    }
    assert payload["nodes"][0]["warnings"] == [
        "`target.shell` uses `kimi` with bash without interactive startup; helpers from `~/.bashrc` are usually unavailable. Add `-i`, set `target.shell_interactive: true`, or use `bash -lic`."
    ]


def test_inspect_command_detects_backtick_eval_kimi_wrapper_in_auto_preflight(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-backtick-eval-wrapper
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: "bash -lc 'eval `kimi` && {command}'"
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"]["auto_preflight"] == {
        "enabled": True,
        "reason": "local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.",
        "matches": [{"node_id": "review", "agent": "claude", "trigger": "target.shell"}],
        "match_summary": ["review (claude) via `target.shell`"],
    }
    assert payload["nodes"][0]["warnings"] == [
        "`target.shell` uses `kimi` with bash without interactive startup; helpers from `~/.bashrc` are usually unavailable. Add `-i`, set `target.shell_interactive: true`, or use `bash -lic`."
    ]


def test_inspect_command_detects_env_var_eval_kimi_wrapper_in_auto_preflight(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-env-eval-wrapper
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: 'bash -lc ''KIMI_ENV="$(kimi)" && eval "$KIMI_ENV" && {command}'''
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"]["auto_preflight"] == {
        "enabled": True,
        "reason": "local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.",
        "matches": [{"node_id": "review", "agent": "claude", "trigger": "target.shell"}],
        "match_summary": ["review (claude) via `target.shell`"],
    }
    assert payload["nodes"][0]["warnings"] == [
        "`target.shell` uses `kimi` with bash without interactive startup; helpers from `~/.bashrc` are usually unavailable. Add `-i`, set `target.shell_interactive: true`, or use `bash -lic`."
    ]


def test_inspect_command_warns_when_bash_env_points_to_guarded_home_bashrc(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text(
        "case $- in\n    *i*) ;;\n      *) return;;\nesac\n\nkimi(){ :; }\n",
        encoding="utf-8",
    )
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-bashenv-bashrc-guard
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: "env BASH_ENV=$HOME/.bashrc bash -c"
      shell_init: kimi
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(agentflow.local_shell.Path, "home", classmethod(lambda cls: home))

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["warnings"] == [
        "`shell_init: kimi` uses bash without interactive startup; helpers from `~/.bashrc` are usually unavailable. Set `target.shell_interactive: true` or use `bash -lic`."
    ]


def test_inspect_command_detects_kimi_agent_in_auto_preflight(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-agent-bootstrap
working_dir: .
nodes:
  - id: review
    agent: kimi
    prompt: hi
    target:
      kind: local
      shell: bash
      shell_init: kimi
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"]["auto_preflight"] == {
        "enabled": True,
        "reason": "local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.",
        "matches": [{"node_id": "review", "agent": "kimi", "trigger": "target.shell_init"}],
        "match_summary": ["review (kimi) via `target.shell_init`"],
    }
    assert payload["nodes"][0]["warnings"] == [
        "`shell_init: kimi` uses bash without interactive startup; helpers from `~/.bashrc` are usually unavailable. Set `target.shell_interactive: true` or use `bash -lic`."
    ]


def test_inspect_command_warns_when_kimi_shell_init_uses_non_bash_shell(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-non-bash-kimi
working_dir: .
nodes:
  - id: review
    agent: claude
    provider: kimi
    prompt: hi
    target:
      kind: local
      shell: sh
      shell_init: kimi
""",
        encoding="utf-8",
    )

    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["warnings"] == [
        "`shell_init: kimi` requires bash-style shell bootstrap, but `target.shell` resolves to `sh`. "
        "Use `shell: bash` with `target.shell_login: true` and `target.shell_interactive: true`, "
        "use `bash -lic`, or export provider variables directly."
    ]


def test_inspect_command_resolves_default_kimi_provider_in_json(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-default-provider
working_dir: .
nodes:
  - id: review
    agent: kimi
    prompt: hi
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["provider"] is None
    assert payload["nodes"][0]["resolved_provider"] == {
        "name": "moonshot",
        "base_url": "https://api.moonshot.ai/v1",
        "api_key_env": "KIMI_API_KEY",
        "wire_api": None,
        "headers": {},
        "env": {},
    }


def test_inspect_command_surfaces_default_kimi_provider_in_json_summary(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-kimi-default-provider-summary
working_dir: .
nodes:
  - id: review
    agent: kimi
    prompt: hi
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["provider"] == "moonshot, key=KIMI_API_KEY, url=https://api.moonshot.ai/v1"


@pytest.mark.parametrize(
    "command",
    [
        ["run"],
        ["smoke"],
    ],
)
def test_run_and_smoke_preflight_warn_when_kimi_shell_init_is_not_interactive(tmp_path, monkeypatch, command):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: kimi-preflight-warning
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: bash
      shell_login: true
      shell_init: kimi
""",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="ok",
            checks=[DoctorCheck(name="kimi_shell_helper", status="ok", detail="ready")],
        ),
    )
    monkeypatch.setattr(
        agentflow.cli,
        "_run_pipeline",
        lambda pipeline, runs_dir, max_concurrent_runs, output: captured.setdefault("pipeline", pipeline.name),
    )

    result = runner.invoke(app, [*command, str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    assert captured == {"pipeline": "kimi-preflight-warning"}
    payload = json.loads(result.stderr or result.stdout)
    assert payload["status"] == "warning"
    assert payload["checks"] == [
        {"name": "kimi_shell_helper", "status": "ok", "detail": "ready"},
        {
            "name": "kimi_shell_bootstrap",
            "status": "warning",
            "detail": "Node `review`: `shell_init: kimi` uses bash without interactive startup; helpers from `~/.bashrc` are usually unavailable. Set `target.shell_interactive: true` or use `bash -lic`.",
        },
    ]


@pytest.mark.parametrize(
    "command",
    [
        ["run"],
        ["smoke"],
    ],
)
def test_run_and_smoke_preflight_fail_when_kimi_shell_init_uses_non_bash_shell(tmp_path, monkeypatch, command):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: kimi-preflight-non-bash
working_dir: .
nodes:
  - id: review
    agent: claude
    provider: kimi
    prompt: hi
    target:
      kind: local
      shell: sh
      shell_init: kimi
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="ok",
            checks=[DoctorCheck(name="kimi_shell_helper", status="ok", detail="ready")],
        ),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic")
    monkeypatch.setattr(
        agentflow.cli,
        "_run_pipeline",
        lambda pipeline, runs_dir, max_concurrent_runs, output: pytest.fail("pipeline should not run"),
    )

    result = runner.invoke(app, [*command, str(pipeline_path), "--output", "json"])

    assert result.exit_code == 1
    payload = json.loads(result.stderr or result.stdout)
    assert payload["status"] == "failed"
    assert payload["checks"] == [
        {"name": "kimi_shell_helper", "status": "ok", "detail": "ready"},
        {
            "name": "kimi_shell_bootstrap",
            "status": "failed",
            "detail": "Node `review`: `shell_init: kimi` requires bash-style shell bootstrap, but `target.shell` resolves to `sh`. Use `shell: bash` with `target.shell_login: true` and `target.shell_interactive: true`, use `bash -lic`, or export provider variables directly.",
        },
    ]


@pytest.mark.parametrize(
    "command",
    [
        ["run"],
        ["smoke"],
    ],
)
def test_run_and_smoke_preflight_skips_codex_auth_probe_when_kimi_shell_init_is_not_interactive(
    tmp_path,
    monkeypatch,
    command,
):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: codex-kimi-preflight-warning
working_dir: .
nodes:
  - id: codex_plan
    agent: codex
    prompt: hi
    target:
      kind: local
      shell: bash
      shell_login: true
      shell_init: kimi
""",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="ok",
            checks=[DoctorCheck(name="kimi_shell_helper", status="ok", detail="ready")],
        ),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("codex auth probe should not run when the kimi shell bootstrap already warns"),
    )
    monkeypatch.setattr(
        agentflow.cli,
        "_run_pipeline",
        lambda pipeline, runs_dir, max_concurrent_runs, output: captured.setdefault("pipeline", pipeline.name),
    )

    result = runner.invoke(app, [*command, str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    assert captured == {"pipeline": "codex-kimi-preflight-warning"}
    payload = json.loads(result.stderr or result.stdout)
    assert payload["status"] == "warning"
    assert payload["checks"] == [
        {"name": "kimi_shell_helper", "status": "ok", "detail": "ready"},
        {
            "name": "kimi_shell_bootstrap",
            "status": "warning",
            "detail": "Node `codex_plan`: `shell_init: kimi` uses bash without interactive startup; helpers from `~/.bashrc` are usually unavailable. Set `target.shell_interactive: true` or use `bash -lic`.",
        },
    ]


@pytest.mark.parametrize(
    "command",
    [
        ["run"],
        ["smoke"],
    ],
)
def test_run_and_smoke_preflight_skips_codex_auth_probe_when_kimi_shell_init_uses_non_bash_shell(
    tmp_path,
    monkeypatch,
    command,
):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: codex-kimi-preflight-non-bash
working_dir: .
nodes:
  - id: codex_plan
    agent: codex
    prompt: hi
    target:
      kind: local
      shell: sh
      shell_init: kimi
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="ok",
            checks=[DoctorCheck(name="kimi_shell_helper", status="ok", detail="ready")],
        ),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("codex auth probe should not run when the kimi shell bootstrap already fails"),
    )
    monkeypatch.setattr(
        agentflow.cli,
        "_run_pipeline",
        lambda pipeline, runs_dir, max_concurrent_runs, output: pytest.fail("pipeline should not run"),
    )

    result = runner.invoke(app, [*command, str(pipeline_path), "--output", "json"])

    assert result.exit_code == 1
    payload = json.loads(result.stderr or result.stdout)
    assert payload["status"] == "failed"
    assert payload["checks"] == [
        {"name": "kimi_shell_helper", "status": "ok", "detail": "ready"},
        {
            "name": "kimi_shell_bootstrap",
            "status": "failed",
            "detail": "Node `codex_plan`: `shell_init: kimi` requires bash-style shell bootstrap, but `target.shell` resolves to `sh`. Use `shell: bash` with `target.shell_login: true` and `target.shell_interactive: true`, use `bash -lic`, or export provider variables directly.",
        },
    ]


@pytest.mark.parametrize(
    "command",
    [
        ["run"],
        ["smoke"],
    ],
)
def test_run_and_smoke_preflight_warn_when_explicit_kimi_shell_wrapper_is_not_interactive(tmp_path, monkeypatch, command):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: kimi-wrapper-preflight-warning
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: "bash -lc 'kimi && {command}'"
""",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="ok",
            checks=[DoctorCheck(name="kimi_shell_helper", status="ok", detail="ready")],
        ),
    )
    monkeypatch.setattr(
        agentflow.cli,
        "_run_pipeline",
        lambda pipeline, runs_dir, max_concurrent_runs, output: captured.setdefault("pipeline", pipeline.name),
    )

    result = runner.invoke(app, [*command, str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    assert captured == {"pipeline": "kimi-wrapper-preflight-warning"}
    payload = json.loads(result.stderr)
    assert payload["status"] == "warning"
    assert payload["checks"] == [
        {"name": "kimi_shell_helper", "status": "ok", "detail": "ready"},
        {
            "name": "kimi_shell_bootstrap",
            "status": "warning",
            "detail": "Node `review`: `target.shell` uses `kimi` with bash without interactive startup; helpers from `~/.bashrc` are usually unavailable. Add `-i`, set `target.shell_interactive: true`, or use `bash -lic`.",
        },
    ]


@pytest.mark.parametrize(
    "command",
    [
        ["run"],
        ["smoke"],
    ],
)
def test_run_and_smoke_preflight_warn_when_eval_style_kimi_wrapper_is_not_interactive(tmp_path, monkeypatch, command):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: kimi-eval-wrapper-preflight-warning
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: "bash -lc 'eval \\\"$(kimi)\\\" && {command}'"
""",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="ok",
            checks=[DoctorCheck(name="kimi_shell_helper", status="ok", detail="ready")],
        ),
    )
    monkeypatch.setattr(
        agentflow.cli,
        "_run_pipeline",
        lambda pipeline, runs_dir, max_concurrent_runs, output: captured.setdefault("pipeline", pipeline.name),
    )

    result = runner.invoke(app, [*command, str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    assert captured == {"pipeline": "kimi-eval-wrapper-preflight-warning"}
    payload = json.loads(result.stderr)
    assert payload["status"] == "warning"
    assert payload["checks"] == [
        {"name": "kimi_shell_helper", "status": "ok", "detail": "ready"},
        {
            "name": "kimi_shell_bootstrap",
            "status": "warning",
            "detail": "Node `review`: `target.shell` uses `kimi` with bash without interactive startup; helpers from `~/.bashrc` are usually unavailable. Add `-i`, set `target.shell_interactive: true`, or use `bash -lic`.",
        },
    ]


@pytest.mark.parametrize(
    "command",
    [
        ["run"],
        ["smoke"],
    ],
)
def test_run_and_smoke_preflight_warn_when_backtick_eval_kimi_wrapper_is_not_interactive(tmp_path, monkeypatch, command):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: kimi-backtick-wrapper-preflight-warning
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      shell: "bash -lc 'eval `kimi` && {command}'"
""",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="ok",
            checks=[DoctorCheck(name="kimi_shell_helper", status="ok", detail="ready")],
        ),
    )
    monkeypatch.setattr(
        agentflow.cli,
        "_run_pipeline",
        lambda pipeline, runs_dir, max_concurrent_runs, output: captured.setdefault("pipeline", pipeline.name),
    )

    result = runner.invoke(app, [*command, str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    assert captured == {"pipeline": "kimi-backtick-wrapper-preflight-warning"}
    payload = json.loads(result.stderr)
    assert payload["status"] == "warning"
    assert payload["checks"] == [
        {"name": "kimi_shell_helper", "status": "ok", "detail": "ready"},
        {
            "name": "kimi_shell_bootstrap",
            "status": "warning",
            "detail": "Node `review`: `target.shell` uses `kimi` with bash without interactive startup; helpers from `~/.bashrc` are usually unavailable. Add `-i`, set `target.shell_interactive: true`, or use `bash -lic`.",
        },
    ]


def test_inspect_command_redacts_auth_and_header_style_env_keys(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-redaction
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    provider:
      name: kimi
      base_url: https://api.kimi.com/coding/
      api_key_env: ANTHROPIC_API_KEY
      env:
        UPSTREAM_AUTH_HEADER: Bearer top-secret
      headers:
        Authorization: Bearer top-secret
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--node", "review", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    env = payload["nodes"][0]["launch"]["env"]
    assert env["ANTHROPIC_API_KEY"] == "<redacted>"
    assert env["ANTHROPIC_CUSTOM_HEADERS"] == "<redacted>"
    assert env["UPSTREAM_AUTH_HEADER"] == "<redacted>"


def test_inspect_command_summary_shows_resolved_provider(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-provider
working_dir: .
nodes:
  - id: review
    agent: claude
    provider: kimi
    model: claude-sonnet-4-5
    prompt: hi
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path)])

    assert result.exit_code == 0
    assert "Model: claude-sonnet-4-5" in result.stdout
    assert "Provider: kimi, key=ANTHROPIC_API_KEY, url=https://api.kimi.com/coding/" in result.stdout


def test_inspect_command_prefers_node_env_auth_source_over_provider_env(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-node-env-auth
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    env:
      ANTHROPIC_API_KEY: node-secret
    provider:
      name: kimi-proxy
      base_url: https://example.test/anthropic
      api_key_env: ANTHROPIC_API_KEY
      env:
        ANTHROPIC_API_KEY: provider-secret
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["nodes"][0]["auth"] == "`ANTHROPIC_API_KEY` via `node.env`"


def test_inspect_command_surfaces_skills_and_mcp_names(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-integrations
working_dir: .
nodes:
  - id: review
    agent: claude
    prompt: hi
    skills: [repo-map, release-notes]
    mcps:
      - name: github
        transport: streamable_http
        url: https://example.com/mcp
      - name: filesystem
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
""",
        encoding="utf-8",
    )

    summary_result = runner.invoke(app, ["inspect", str(pipeline_path)])

    assert summary_result.exit_code == 0
    assert "Skills: repo-map, release-notes" in summary_result.stdout
    assert "MCPs: github, filesystem" in summary_result.stdout

    json_summary_result = runner.invoke(app, ["inspect", str(pipeline_path), "--output", "json-summary"])

    assert json_summary_result.exit_code == 0
    payload = json.loads(json_summary_result.stdout)
    assert payload["nodes"][0]["skills"] == ["repo-map", "release-notes"]
    assert payload["nodes"][0]["mcps"] == ["github", "filesystem"]


def test_inspect_command_rejects_unknown_nodes(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: inspect-error
working_dir: .
nodes:
  - id: alpha
    agent: codex
    prompt: hi
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--node", "missing"])

    assert result.exit_code != 0
    assert "unknown node ids: ['missing']" in result.stderr


def test_serve_uses_runtime_env_vars(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setenv("AGENTFLOW_RUNS_DIR", "/tmp/agentflow-env-runs")
    monkeypatch.setenv("AGENTFLOW_MAX_CONCURRENT_RUNS", "7")

    def fake_build_runtime(runs_dir: str, max_concurrent_runs: int):
        captured["runs_dir"] = runs_dir
        captured["max_concurrent_runs"] = max_concurrent_runs
        return object(), object()

    monkeypatch.setattr(agentflow.cli, "_build_runtime", fake_build_runtime)
    monkeypatch.setattr(agentflow.cli, "_create_web_app", lambda store, orchestrator: "fake-app")
    monkeypatch.setattr(agentflow.cli, "_serve_web_app", lambda app, host, port: captured.update({"app": app, "host": host, "port": port}))

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0
    assert captured == {
        "runs_dir": "/tmp/agentflow-env-runs",
        "max_concurrent_runs": 7,
        "app": "fake-app",
        "host": "127.0.0.1",
        "port": 8000,
    }


def test_run_uses_runtime_env_vars(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setenv("AGENTFLOW_RUNS_DIR", "/tmp/agentflow-env-runs")
    monkeypatch.setenv("AGENTFLOW_MAX_CONCURRENT_RUNS", "9")

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="run-123")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return SimpleNamespace(
                status=SimpleNamespace(value="completed"),
                model_dump=lambda mode="json": {"id": run_id, "status": "completed"},
            )

    def fake_build_runtime(runs_dir: str, max_concurrent_runs: int):
        captured["runs_dir"] = runs_dir
        captured["max_concurrent_runs"] = max_concurrent_runs
        return object(), FakeOrchestrator()

    fake_pipeline = object()
    monkeypatch.setattr(agentflow.cli, "_build_runtime", fake_build_runtime)
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: fake_pipeline)

    result = runner.invoke(app, ["run", "pipeline.yaml"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"id": "run-123", "status": "completed"}
    assert captured["runs_dir"] == "/tmp/agentflow-env-runs"
    assert captured["max_concurrent_runs"] == 9
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "run-123"
    assert captured["wait_timeout"] is None


def test_run_supports_summary_output(monkeypatch):
    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            return SimpleNamespace(id="run-summary")

        async def wait(self, run_id: str, timeout: float | None = None):
            return _completed_run(
                run_id,
                pipeline_name="summary-pipeline",
                pipeline_nodes=[
                    SimpleNamespace(
                        id="codex_plan",
                        agent=SimpleNamespace(value="codex"),
                        model="gpt-5-codex",
                        provider=None,
                    )
                ],
                nodes={
                    "codex_plan": SimpleNamespace(
                        status=SimpleNamespace(value="completed"),
                        current_attempt=1,
                        attempts=[SimpleNamespace(number=1)],
                        exit_code=0,
                        final_response="codex ok",
                        output="codex ok",
                        stderr_lines=[],
                    )
                },
            )

    monkeypatch.setattr(agentflow.cli, "_build_runtime", lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()))
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: object())

    result = runner.invoke(app, ["run", "pipeline.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert "Run run-summary: completed" in result.stdout
    assert "Pipeline: summary-pipeline" in result.stdout
    assert "Run dir: .agentflow/runs/run-summary" in result.stdout
    assert "- codex_plan [codex, model=gpt-5-codex]: completed (attempt 1, exit 0) - codex ok" in result.stdout


def test_run_supports_json_summary_output(monkeypatch):
    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            return SimpleNamespace(id="run-json-summary")

        async def wait(self, run_id: str, timeout: float | None = None):
            return _completed_run(
                run_id,
                pipeline_name="summary-pipeline",
                pipeline_nodes=[
                    SimpleNamespace(
                        id="codex_plan",
                        agent=SimpleNamespace(value="codex"),
                        model="gpt-5-codex",
                        provider=None,
                    )
                ],
                nodes={
                    "codex_plan": SimpleNamespace(
                        status=SimpleNamespace(value="completed"),
                        current_attempt=1,
                        attempts=[SimpleNamespace(number=1)],
                        exit_code=0,
                        final_response="codex ok",
                        output="codex ok",
                        stderr_lines=[],
                    )
                },
            )

    monkeypatch.setattr(agentflow.cli, "_build_runtime", lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()))
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: object())

    result = runner.invoke(app, ["run", "pipeline.yaml", "--output", "json-summary"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "id": "run-json-summary",
        "status": "completed",
        "pipeline": {"name": "summary-pipeline"},
        "started_at": "2026-03-08T04:11:03+00:00",
        "finished_at": "2026-03-08T04:11:10+00:00",
        "duration": "7.0s",
        "duration_seconds": 7.0,
        "run_dir": ".agentflow/runs/run-json-summary",
        "nodes": [
            {
                "id": "codex_plan",
                "status": "completed",
                "agent": "codex",
                "model": "gpt-5-codex",
                "attempts": 1,
                "exit_code": 0,
                "preview": "codex ok",
            }
        ],
    }


def test_run_skips_preflight_for_custom_pipeline_in_auto_mode(monkeypatch):
    captured: dict[str, object] = {}

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="run-custom")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="custom-run")

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: (_ for _ in ()).throw(AssertionError("doctor should not run")))
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    fake_pipeline = object()
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["run", "custom-run.yaml"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"id": "run-custom", "status": "completed"}
    assert captured["loaded_path"] == "custom-run.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "run-custom"
    assert captured["wait_timeout"] is None


def test_run_auto_runs_preflight_for_custom_pipeline_with_kimi_shell_init(monkeypatch):
    captured: dict[str, object] = {}
    doctor_calls = 0

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="run-custom-kimi")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="custom-kimi-run")

    def fake_doctor_report():
        nonlocal doctor_calls
        doctor_calls += 1
        return _doctor_report()

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", fake_doctor_report)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                agent=SimpleNamespace(value="codex"),
                target=SimpleNamespace(kind="local", shell="bash", shell_init="kimi"),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["run", "custom-run.yaml"])

    assert result.exit_code == 0
    assert doctor_calls == 1
    assert captured["loaded_path"] == "custom-run.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "run-custom-kimi"
    assert captured["wait_timeout"] is None


def test_run_show_preflight_prints_successful_summary_to_stderr(monkeypatch):
    captured: dict[str, object] = {}

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="run-show-preflight")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="custom-kimi-run")

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="codex_plan",
                agent=SimpleNamespace(value="codex"),
                target=SimpleNamespace(kind="local", shell="bash", shell_interactive=True, shell_init="kimi"),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["run", "custom-run.yaml", "--output", "summary", "--show-preflight"])

    assert result.exit_code == 0
    assert result.stderr == (
        "Doctor: ok\n"
        "- kimi_shell_helper: ok - ready\n"
        "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.\n"
        "Pipeline auto preflight matches: codex_plan (codex) via `target.shell_init`\n"
    )
    assert "Run run-show-preflight: completed" in result.stdout
    assert captured["loaded_path"] == "custom-run.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "run-show-preflight"
    assert captured["wait_timeout"] is None


def test_run_auto_preflight_stops_when_local_codex_auth_is_unavailable(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def fake_run(*args, **kwargs):
        env = kwargs.get("env") or {}
        target_command = env.get("AGENTFLOW_TARGET_COMMAND", "")
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0 if target_command == "codex --version" else 1,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runtime should not build when preflight fails")),
    )
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="codex_plan",
                agent=SimpleNamespace(value="codex"),
                provider=None,
                env={},
                executable=None,
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["run", "custom-run.yaml", "--output", "summary"])

    assert result.exit_code == 1
    assert captured["loaded_path"] == "custom-run.yaml"
    assert result.stdout == (
        "Doctor: failed\n"
        "- kimi_shell_helper: ok - ready\n"
        "- codex_auth: failed - Node `codex_plan` (codex) cannot authenticate local Codex after the node shell bootstrap; `codex login status` fails and `OPENAI_API_KEY` is not set in the current environment, `node.env`, or `provider.env`.\n"
        "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.\n"
        "Pipeline auto preflight matches: codex_plan (codex) via `target.shell_init`\n"
    )


def test_run_auto_preflight_stops_when_local_codex_is_unavailable_after_shell_bootstrap(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runtime should not build when preflight fails")),
    )
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="codex_plan",
                agent=SimpleNamespace(value="codex"),
                provider=None,
                env={},
                executable=None,
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["run", "custom-run.yaml", "--output", "summary"])

    assert result.exit_code == 1
    assert captured["loaded_path"] == "custom-run.yaml"
    assert result.stdout == (
        "Doctor: failed\n"
        "- kimi_shell_helper: ok - ready\n"
        "- codex_ready: failed - Node `codex_plan` (codex) cannot launch local Codex after the node shell bootstrap; `codex --version` fails in the prepared local shell.\n"
        "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.\n"
        "Pipeline auto preflight matches: codex_plan (codex) via `target.shell_init`\n"
    )


def test_run_auto_preflight_stops_when_local_claude_is_unavailable_after_shell_bootstrap(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(subprocess, "run", _completed_subprocess(returncode=1))
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runtime should not build when preflight fails")),
    )
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider="kimi",
                env={},
                executable=None,
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["run", "custom-run.yaml", "--output", "summary"])

    assert result.exit_code == 1
    assert captured["loaded_path"] == "custom-run.yaml"
    assert result.stdout == (
        "Doctor: failed\n"
        "- kimi_shell_helper: ok - ready\n"
        "- claude_ready: failed - Node `claude_review` (claude) cannot launch local Claude after the node shell bootstrap; `claude --version` fails in the prepared local shell.\n"
        "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.\n"
        "Pipeline auto preflight matches: claude_review (claude) via `target.shell_init`\n"
    )


def test_run_auto_runs_preflight_for_custom_pipeline_with_kimi_agent(monkeypatch):
    captured: dict[str, object] = {}
    doctor_calls = 0

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="run-custom-kimi-agent")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="custom-kimi-agent-run")

    def fake_doctor_report():
        nonlocal doctor_calls
        doctor_calls += 1
        return _doctor_report()

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", fake_doctor_report)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                agent=SimpleNamespace(value="kimi"),
                env={"KIMI_API_KEY": "inline-secret"},
                target=SimpleNamespace(kind="local", shell="bash", shell_init="kimi"),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["run", "custom-run.yaml"])

    assert result.exit_code == 0
    assert doctor_calls == 1
    assert captured["loaded_path"] == "custom-run.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "run-custom-kimi-agent"
    assert captured["wait_timeout"] is None


def test_run_auto_runs_preflight_for_custom_pipeline_with_shell_init_command_list(monkeypatch):
    captured: dict[str, object] = {}
    doctor_calls = 0

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="run-custom-kimi-shell-init-list")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="custom-kimi-shell-init-list")

    def fake_doctor_report():
        nonlocal doctor_calls
        doctor_calls += 1
        return _doctor_report()

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", fake_doctor_report)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                agent=SimpleNamespace(value="codex"),
                target=SimpleNamespace(kind="local", shell="bash", shell_init=["command -v kimi >/dev/null 2>&1", "kimi"]),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["run", "custom-run.yaml"])

    assert result.exit_code == 0
    assert doctor_calls == 1
    assert captured["loaded_path"] == "custom-run.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "run-custom-kimi-shell-init-list"
    assert captured["wait_timeout"] is None


def test_run_auto_ignores_non_helper_kimi_substrings(monkeypatch):
    captured: dict[str, object] = {}

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="run-custom-non-kimi")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="custom-non-kimi-run")

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: (_ for _ in ()).throw(AssertionError("doctor should not run")))
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                agent=SimpleNamespace(value="codex"),
                target=SimpleNamespace(kind="local", shell="bash -lc 'printf kimi-ready'", shell_init="echo kimi-ready"),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["run", "custom-run.yaml"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-run.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "run-custom-non-kimi"
    assert captured["wait_timeout"] is None


def test_run_runs_preflight_for_explicit_bundled_pipeline_path(monkeypatch):
    captured: dict[str, object] = {}
    doctor_calls = 0

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="run-explicit-default")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="local-real-agents-kimi-smoke")

    def fake_doctor_report():
        nonlocal doctor_calls
        doctor_calls += 1
        return _doctor_report()

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", fake_doctor_report)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    bundled_path = str((Path.cwd() / "examples/local-real-agents-kimi-smoke.yaml").resolve())
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: bundled_path)
    fake_pipeline = object()
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["run", bundled_path])

    assert result.exit_code == 0
    assert doctor_calls == 1
    assert captured["loaded_path"] == bundled_path
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "run-explicit-default"
    assert captured["wait_timeout"] is None


@pytest.mark.parametrize(
    "command",
    [
        ["run"],
        ["smoke"],
    ],
)
def test_run_and_smoke_bundled_preflight_applies_pipeline_shell_checks(monkeypatch, command):
    bundled_path = str((Path.cwd() / "examples/local-real-agents-kimi-smoke.yaml").resolve())

    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="ok",
            checks=[DoctorCheck(name="kimi_shell_helper", status="ok", detail="ready")],
        ),
    )
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: bundled_path)
    monkeypatch.setattr(
        agentflow.cli,
        "_run_pipeline",
        lambda pipeline, runs_dir, max_concurrent_runs, output: pytest.fail("pipeline should not run"),
    )

    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="review",
                agent=SimpleNamespace(value="claude"),
                provider="kimi",
                env={},
                target=SimpleNamespace(
                    kind="local",
                    shell="sh",
                    shell_init="kimi",
                    shell_login=False,
                    shell_interactive=False,
                    cwd=None,
                ),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: fake_pipeline)

    args = [*command, bundled_path, "--output", "json"] if command[0] == "run" else [*command, "--output", "json"]
    if command[0] == "smoke":
        args = [*command, "--output", "json"]

    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "status": "failed",
        "checks": [
            {"name": "kimi_shell_helper", "status": "ok", "detail": "ready"},
            {
                "name": "kimi_shell_bootstrap",
                "status": "failed",
                "detail": "Node `review`: `shell_init: kimi` requires bash-style shell bootstrap, but `target.shell` resolves to `sh`. Use `shell: bash` with `target.shell_login: true` and `target.shell_interactive: true`, use `bash -lic`, or export provider variables directly.",
            },
        ],
        "pipeline": {
            "auto_preflight": {
                "enabled": True,
                "reason": "path matches the bundled real-agent smoke pipeline.",
                "matches": [
                    {
                        "node_id": "review",
                        "agent": "claude",
                        "trigger": "target.shell_init",
                    }
                ],
                "match_summary": ["review (claude) via `target.shell_init`"],
            }
        },
    }


def test_run_can_disable_preflight_for_bundled_pipeline(monkeypatch):
    captured: dict[str, object] = {}

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="run-no-preflight")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="local-real-agents-kimi-smoke")

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: (_ for _ in ()).throw(AssertionError("doctor should not run")))
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    bundled_path = str((Path.cwd() / "examples/local-real-agents-kimi-smoke.yaml").resolve())
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: bundled_path)
    fake_pipeline = object()
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["run", bundled_path, "--preflight", "never"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == bundled_path
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "run-no-preflight"
    assert captured["wait_timeout"] is None


def test_run_stops_when_detected_preflight_fails(monkeypatch):
    bundled_path = str((Path.cwd() / "examples/local-real-agents-kimi-smoke.yaml").resolve())

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report(status="failed", detail="missing"))
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: bundled_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: (_ for _ in ()).throw(AssertionError("pipeline should not load")))
    monkeypatch.setattr(agentflow.cli, "_build_runtime", lambda runs_dir, max_concurrent_runs: (_ for _ in ()).throw(AssertionError("runtime should not build")))

    result = runner.invoke(app, ["run", bundled_path])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "status": "failed",
        "checks": [{"name": "kimi_shell_helper", "status": "failed", "detail": "missing"}],
    }


def test_smoke_uses_bundled_pipeline_by_default(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setenv("AGENTFLOW_RUNS_DIR", "/tmp/agentflow-smoke-runs")
    monkeypatch.setenv("AGENTFLOW_MAX_CONCURRENT_RUNS", "5")

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="smoke-123")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(
                run_id,
                pipeline_name="local-real-agents-kimi-smoke",
                pipeline_nodes=[
                    SimpleNamespace(
                        id="codex_plan",
                        agent=SimpleNamespace(value="codex"),
                        model=None,
                        provider=None,
                    ),
                    SimpleNamespace(
                        id="claude_review",
                        agent=SimpleNamespace(value="claude"),
                        model=None,
                        provider="kimi",
                    ),
                ],
                nodes={
                    "codex_plan": SimpleNamespace(
                        status=SimpleNamespace(value="completed"),
                        current_attempt=1,
                        attempts=[SimpleNamespace(number=1)],
                        exit_code=0,
                        final_response="codex ok",
                        output="codex ok",
                        stderr_lines=[],
                    ),
                    "claude_review": SimpleNamespace(
                        status=SimpleNamespace(value="completed"),
                        current_attempt=1,
                        attempts=[SimpleNamespace(number=1)],
                        exit_code=0,
                        final_response="claude ok",
                        output="claude ok",
                        stderr_lines=[],
                    ),
                },
            )

    def fake_build_runtime(runs_dir: str, max_concurrent_runs: int):
        captured["runs_dir"] = runs_dir
        captured["max_concurrent_runs"] = max_concurrent_runs
        return object(), FakeOrchestrator()

    fake_pipeline = object()
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(agentflow.cli, "_build_runtime", lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), fake_build_runtime(runs_dir, max_concurrent_runs)[1]))
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["smoke"])

    assert result.exit_code == 0
    assert "Run smoke-123: completed" in result.stdout
    assert "Pipeline: local-real-agents-kimi-smoke" in result.stdout
    assert "- codex_plan [codex]: completed (attempt 1, exit 0) - codex ok" in result.stdout
    assert "- claude_review [claude, provider=kimi]: completed (attempt 1, exit 0) - claude ok" in result.stdout
    assert captured["loaded_path"] == "examples/local-real-agents-kimi-smoke.yaml"
    assert captured["runs_dir"] == "/tmp/agentflow-smoke-runs"
    assert captured["max_concurrent_runs"] == 5
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "smoke-123"
    assert captured["wait_timeout"] is None


def test_check_local_uses_bundled_pipeline_by_default(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setenv("AGENTFLOW_RUNS_DIR", "/tmp/agentflow-check-local-runs")
    monkeypatch.setenv("AGENTFLOW_MAX_CONCURRENT_RUNS", "4")

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="check-local-123")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="local-real-agents-kimi-smoke")

    def fake_build_runtime(runs_dir: str, max_concurrent_runs: int):
        captured["runs_dir"] = runs_dir
        captured["max_concurrent_runs"] = max_concurrent_runs
        return object(), FakeOrchestrator()

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (
            SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id),
            fake_build_runtime(runs_dir, max_concurrent_runs)[1],
        ),
    )

    bundled_path = str((Path.cwd() / "examples/local-real-agents-kimi-smoke.yaml").resolve())
    fake_pipeline = object()
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: bundled_path)
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["check-local"])

    assert result.exit_code == 0
    assert result.stderr == (
        "Doctor: ok\n"
        "- kimi_shell_helper: ok - ready\n"
        "Pipeline auto preflight: enabled - path matches the bundled real-agent smoke pipeline.\n"
    )
    assert "Run check-local-123: completed" in result.stdout
    assert captured["loaded_path"] == bundled_path
    assert captured["runs_dir"] == "/tmp/agentflow-check-local-runs"
    assert captured["max_concurrent_runs"] == 4
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "check-local-123"
    assert captured["wait_timeout"] is None


def test_check_local_reuses_preflight_loaded_pipeline(monkeypatch):
    captured: dict[str, object] = {}

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="check-local-reuse")

        async def wait(self, run_id: str, timeout: float | None = None):
            return _completed_run(run_id, pipeline_name="local-real-agents-kimi-smoke")

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")

    loaded_pipelines = [object(), object()]

    def fake_load(path: str):
        captured["loaded_path"] = path
        captured["load_calls"] = int(captured.get("load_calls", 0)) + 1
        pipeline = loaded_pipelines.pop(0)
        captured.setdefault("loaded_pipelines", []).append(pipeline)
        return pipeline

    monkeypatch.setattr(agentflow.cli, "_load_pipeline", fake_load)

    result = runner.invoke(app, ["check-local"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "examples/local-real-agents-kimi-smoke.yaml"
    assert captured["load_calls"] == 1
    assert captured["submitted_pipeline"] is captured["loaded_pipelines"][0]
    assert len(loaded_pipelines) == 1


def test_check_local_uses_json_doctor_output_when_run_output_is_json(monkeypatch):
    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            return SimpleNamespace(id="check-local-json")

        async def wait(self, run_id: str, timeout: float | None = None):
            return _completed_run(run_id, pipeline_name="local-real-agents-kimi-smoke")

    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="warning",
            checks=[DoctorCheck(name="bash_login_startup", status="warning", detail="missing bridge")],
        ),
    )
    monkeypatch.setattr(agentflow.cli, "build_bash_login_shell_bridge_recommendation", _shell_bridge_recommendation)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: object())

    result = runner.invoke(app, ["check-local", "--output", "json", "--shell-bridge"])

    assert result.exit_code == 0
    assert json.loads(result.stderr) == {
        "status": "warning",
        "checks": [{"name": "bash_login_startup", "status": "warning", "detail": "missing bridge"}],
        "pipeline": {
            "auto_preflight": {
                "enabled": True,
                "reason": "path matches the bundled real-agent smoke pipeline.",
                "matches": [],
                "match_summary": [],
            }
        },
        "shell_bridge": _shell_bridge_recommendation().as_dict(),
    }
    assert json.loads(result.stdout) == {"id": "check-local-json", "status": "completed"}


def test_check_local_stops_when_doctor_fails(monkeypatch):
    bundled_path = str((Path.cwd() / "examples/local-real-agents-kimi-smoke.yaml").resolve())

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report(status="failed", detail="missing"))
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: bundled_path)
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader({}, object()))
    monkeypatch.setattr(agentflow.cli, "_build_runtime", lambda runs_dir, max_concurrent_runs: (_ for _ in ()).throw(AssertionError("runtime should not build")))

    result = runner.invoke(app, ["check-local"])

    assert result.exit_code == 1
    assert result.stderr == (
        "Doctor: failed\n"
        "- kimi_shell_helper: failed - missing\n"
        "Pipeline auto preflight: enabled - path matches the bundled real-agent smoke pipeline.\n"
    )
    assert result.stdout == ""


def test_doctor_custom_non_kimi_pipeline_skips_bundled_smoke_prereqs(monkeypatch):
    fake_pipeline = SimpleNamespace(nodes=[])

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report(status="failed", detail="missing kimi"))
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: fake_pipeline)

    result = runner.invoke(app, ["doctor", "examples/custom.yaml", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "status": "ok",
        "checks": [],
        "pipeline": {
            "auto_preflight": {
                "enabled": False,
                "reason": "path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.",
                "matches": [],
                "match_summary": [],
            }
        },
    }


def test_check_local_custom_non_kimi_pipeline_skips_bundled_smoke_prereqs(monkeypatch):
    captured: dict[str, object] = {}
    fake_pipeline = SimpleNamespace(nodes=[])

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="check-local-custom")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            return _completed_run(run_id, pipeline_name="custom")

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report(status="failed", detail="missing kimi"))
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: fake_pipeline)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (
            SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id),
            FakeOrchestrator(),
        ),
    )

    result = runner.invoke(app, ["check-local", "examples/custom.yaml"])

    assert result.exit_code == 0
    assert result.stderr == (
        "Doctor: ok\n"
        "Pipeline auto preflight: disabled - path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.\n"
    )
    assert "Run check-local-custom: completed" in result.stdout
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "check-local-custom"


def test_smoke_runs_when_bundled_preflight_warns(monkeypatch):
    captured: dict[str, object] = {}

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="smoke-warning")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="local-real-agents-kimi-smoke")

    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="warning",
            checks=[DoctorCheck(name="claude", status="warning", detail="bootstrap-only")],
        ),
    )
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    fake_pipeline = object()
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["smoke"])

    assert result.exit_code == 0
    assert "Run smoke-warning: completed" in result.stdout
    assert result.stderr == (
        "Doctor: warning\n"
        "- claude: warning - bootstrap-only\n"
        "Pipeline auto preflight: enabled - path matches the bundled real-agent smoke pipeline.\n"
    )
    assert captured["loaded_path"] == "examples/local-real-agents-kimi-smoke.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "smoke-warning"
    assert captured["wait_timeout"] is None


def test_smoke_keeps_expected_launch_env_override_out_of_preflight_warning_state(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "bundled-smoke.yaml"
    pipeline_path.write_text(
        """name: bundled-smoke
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
    captured: dict[str, object] = {}

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="smoke-launch-env-warning")

        async def wait(self, run_id: str, timeout: float | None = None):
            return _completed_run(run_id, pipeline_name="bundled-smoke")

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: str(pipeline_path))
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic")

    result = runner.invoke(app, ["smoke"])

    assert result.exit_code == 0
    assert "Run smoke-launch-env-warning: completed" in result.stdout
    assert result.stderr == ""
    assert getattr(captured["submitted_pipeline"], "name", None) == "bundled-smoke"


def test_smoke_show_preflight_prints_successful_summary_to_stderr(monkeypatch):
    captured: dict[str, object] = {}

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="smoke-show-preflight")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="local-real-agents-kimi-smoke")

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="codex_plan",
                agent=SimpleNamespace(value="codex"),
                target=SimpleNamespace(kind="local", shell="bash", shell_login=True, shell_interactive=True, shell_init="kimi"),
            ),
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                target=SimpleNamespace(kind="local", shell="bash", shell_login=True, shell_interactive=True, shell_init="kimi"),
            ),
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["smoke", "--show-preflight"])

    assert result.exit_code == 0
    assert result.stderr == (
        "Doctor: ok\n"
        "- kimi_shell_helper: ok - ready\n"
        "Pipeline auto preflight: enabled - path matches the bundled real-agent smoke pipeline.\n"
        "Pipeline auto preflight matches: codex_plan (codex) via `target.shell_init`, claude_review (claude) via `target.shell_init`\n"
    )
    assert "Run smoke-show-preflight: completed" in result.stdout
    assert captured["loaded_path"] == "examples/local-real-agents-kimi-smoke.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "smoke-show-preflight"
    assert captured["wait_timeout"] is None


def test_smoke_show_preflight_keeps_json_stdout_machine_readable(monkeypatch):
    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            return SimpleNamespace(id="smoke-show-preflight-json")

        async def wait(self, run_id: str, timeout: float | None = None):
            return _completed_run(run_id, pipeline_name="local-real-agents-kimi-smoke")

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setattr(
        agentflow.cli,
        "_load_pipeline",
        lambda path: SimpleNamespace(
            nodes=[
                SimpleNamespace(
                    id="codex_plan",
                    agent=SimpleNamespace(value="codex"),
                    target=SimpleNamespace(kind="local", shell="bash", shell_login=True, shell_interactive=True, shell_init="kimi"),
                ),
                SimpleNamespace(
                    id="claude_review",
                    agent=SimpleNamespace(value="claude"),
                    target=SimpleNamespace(kind="local", shell="bash", shell_login=True, shell_interactive=True, shell_init="kimi"),
                ),
            ]
        ),
    )

    result = runner.invoke(app, ["smoke", "--output", "json", "--show-preflight"])

    assert result.exit_code == 0
    assert result.stderr == (
        "Doctor: ok\n"
        "- kimi_shell_helper: ok - ready\n"
        "Pipeline auto preflight: enabled - path matches the bundled real-agent smoke pipeline.\n"
        "Pipeline auto preflight matches: codex_plan (codex) via `target.shell_init`, claude_review (claude) via `target.shell_init`\n"
    )
    assert json.loads(result.stdout) == {"id": "smoke-show-preflight-json", "status": "completed"}


def test_smoke_warn_preflight_includes_shell_bridge_summary_when_available(monkeypatch):
    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            return SimpleNamespace(id="smoke-warning-shell-bridge")

        async def wait(self, run_id: str, timeout: float | None = None):
            return _completed_run(run_id, pipeline_name="local-real-agents-kimi-smoke")

    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="warning",
            checks=[DoctorCheck(name="bash_login_startup", status="warning", detail="missing bridge")],
        ),
    )
    monkeypatch.setattr(agentflow.cli, "build_bash_login_shell_bridge_recommendation", _shell_bridge_recommendation)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: object())

    result = runner.invoke(app, ["smoke"])

    assert result.exit_code == 0
    assert result.stderr == (
        "Doctor: warning\n"
        "- bash_login_startup: warning - missing bridge\n"
        "Pipeline auto preflight: enabled - path matches the bundled real-agent smoke pipeline.\n"
        "Shell bridge suggestion for `~/.bash_profile` from `~/.profile`:\n"
        "Reason: Bash login shells use `~/.bash_profile`, so `~/.profile` never runs.\n"
        "if [ -f \"$HOME/.profile\" ]; then\n"
        "  . \"$HOME/.profile\"\n"
        "fi\n"
    )


def test_smoke_warn_preflight_honors_json_output(monkeypatch):
    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            return SimpleNamespace(id="smoke-warning-json")

        async def wait(self, run_id: str, timeout: float | None = None):
            return _completed_run(run_id, pipeline_name="local-real-agents-kimi-smoke")

    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="warning",
            checks=[DoctorCheck(name="claude", status="warning", detail="bootstrap-only")],
        ),
    )
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: object())

    result = runner.invoke(app, ["smoke", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stderr) == {
        "status": "warning",
        "checks": [{"name": "claude", "status": "warning", "detail": "bootstrap-only"}],
        "pipeline": {
            "auto_preflight": {
                "enabled": True,
                "reason": "path matches the bundled real-agent smoke pipeline.",
                "matches": [],
                "match_summary": [],
            }
        },
    }


def test_smoke_warn_preflight_includes_shell_bridge_json_when_available(monkeypatch):
    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            return SimpleNamespace(id="smoke-warning-json-shell-bridge")

        async def wait(self, run_id: str, timeout: float | None = None):
            return _completed_run(run_id, pipeline_name="local-real-agents-kimi-smoke")

    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="warning",
            checks=[DoctorCheck(name="bash_login_startup", status="warning", detail="missing bridge")],
        ),
    )
    monkeypatch.setattr(agentflow.cli, "build_bash_login_shell_bridge_recommendation", _shell_bridge_recommendation)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: object())

    result = runner.invoke(app, ["smoke", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stderr) == {
        "status": "warning",
        "checks": [{"name": "bash_login_startup", "status": "warning", "detail": "missing bridge"}],
        "pipeline": {
            "auto_preflight": {
                "enabled": True,
                "reason": "path matches the bundled real-agent smoke pipeline.",
                "matches": [],
                "match_summary": [],
            }
        },
        "shell_bridge": _shell_bridge_recommendation().as_dict(),
    }


def test_smoke_skips_preflight_for_custom_pipeline_in_auto_mode(monkeypatch):
    captured: dict[str, object] = {}

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="smoke-custom")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="custom-smoke")

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: (_ for _ in ()).throw(AssertionError("doctor should not run")))
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    fake_pipeline = object()
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["smoke", "custom-smoke.yaml"])

    assert result.exit_code == 0
    assert "Run smoke-custom: completed" in result.stdout
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "smoke-custom"
    assert captured["wait_timeout"] is None


def test_smoke_auto_runs_preflight_for_custom_pipeline_with_kimi_shell_init(monkeypatch):
    captured: dict[str, object] = {}
    doctor_calls = 0

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="smoke-custom-kimi-shell-init")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="custom-kimi-smoke")

    def fake_doctor_report():
        nonlocal doctor_calls
        doctor_calls += 1
        return _doctor_report()

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", fake_doctor_report)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                agent=SimpleNamespace(value="codex"),
                target=SimpleNamespace(kind="local", shell="bash", shell_init="kimi"),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["smoke", "custom-smoke.yaml"])

    assert result.exit_code == 0
    assert doctor_calls == 1
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "smoke-custom-kimi-shell-init"
    assert captured["wait_timeout"] is None


def test_smoke_auto_runs_preflight_for_custom_claude_kimi_provider_without_host_anthropic_key(monkeypatch):
    captured: dict[str, object] = {}
    doctor_calls = 0

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="smoke-custom-claude-kimi-provider")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="custom-claude-kimi-provider-smoke")

    def fake_doctor_report():
        nonlocal doctor_calls
        doctor_calls += 1
        return _doctor_report()

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", fake_doctor_report)
    monkeypatch.setattr(subprocess, "run", _completed_subprocess())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider="kimi",
                env={},
                target=SimpleNamespace(kind="local", shell="bash", shell_init="kimi", shell_interactive=True),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["smoke", "custom-smoke.yaml"])

    assert result.exit_code == 0
    assert doctor_calls == 1
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "smoke-custom-claude-kimi-provider"
    assert captured["wait_timeout"] is None


def test_smoke_auto_runs_preflight_for_custom_pipeline_with_kimi_agent(monkeypatch):
    captured: dict[str, object] = {}
    doctor_calls = 0

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="smoke-custom-kimi-agent")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="custom-kimi-agent-smoke")

    def fake_doctor_report():
        nonlocal doctor_calls
        doctor_calls += 1
        return _doctor_report()

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", fake_doctor_report)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                agent=SimpleNamespace(value="kimi"),
                env={"KIMI_API_KEY": "inline-secret"},
                target=SimpleNamespace(kind="local", shell="bash", shell_init="kimi"),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["smoke", "custom-smoke.yaml"])

    assert result.exit_code == 0
    assert doctor_calls == 1
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "smoke-custom-kimi-agent"
    assert captured["wait_timeout"] is None


def test_smoke_auto_runs_preflight_for_custom_pipeline_with_explicit_kimi_shell_wrapper(monkeypatch):
    captured: dict[str, object] = {}
    doctor_calls = 0

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="smoke-custom-kimi-wrapper")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="custom-kimi-shell-wrapper")

    def fake_doctor_report():
        nonlocal doctor_calls
        doctor_calls += 1
        return _doctor_report()

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", fake_doctor_report)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                agent=SimpleNamespace(value="claude"),
                target=SimpleNamespace(kind="local", shell="bash -lic 'kimi && {command}'", shell_init=None),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["smoke", "custom-smoke.yaml"])

    assert result.exit_code == 0
    assert doctor_calls == 1
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "smoke-custom-kimi-wrapper"
    assert captured["wait_timeout"] is None


def test_smoke_auto_runs_preflight_for_custom_pipeline_with_backtick_eval_kimi_shell_wrapper(monkeypatch):
    captured: dict[str, object] = {}
    doctor_calls = 0

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="smoke-custom-kimi-backtick-wrapper")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="custom-kimi-backtick-shell-wrapper")

    def fake_doctor_report():
        nonlocal doctor_calls
        doctor_calls += 1
        return _doctor_report()

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", fake_doctor_report)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                agent=SimpleNamespace(value="claude"),
                target=SimpleNamespace(kind="local", shell="bash -lic 'eval `kimi` && {command}'", shell_init=None),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["smoke", "custom-smoke.yaml"])

    assert result.exit_code == 0
    assert doctor_calls == 1
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "smoke-custom-kimi-backtick-wrapper"
    assert captured["wait_timeout"] is None


def test_smoke_runs_preflight_for_explicit_bundled_pipeline_path(monkeypatch):
    captured: dict[str, object] = {}
    doctor_calls = 0

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="smoke-explicit-default")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="local-real-agents-kimi-smoke")

    def fake_doctor_report():
        nonlocal doctor_calls
        doctor_calls += 1
        return DoctorReport(
            status="warning",
            checks=[DoctorCheck(name="claude", status="warning", detail="bootstrap-only")],
        )

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", fake_doctor_report)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    bundled_path = str((Path.cwd() / "examples/local-real-agents-kimi-smoke.yaml").resolve())
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: bundled_path)
    fake_pipeline = object()
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["smoke", bundled_path])

    assert result.exit_code == 0
    assert doctor_calls == 1
    assert result.stderr == (
        "Doctor: warning\n"
        "- claude: warning - bootstrap-only\n"
        "Pipeline auto preflight: enabled - path matches the bundled real-agent smoke pipeline.\n"
    )
    assert captured["loaded_path"] == bundled_path
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "smoke-explicit-default"
    assert captured["wait_timeout"] is None


def test_smoke_can_force_preflight_for_custom_pipeline(monkeypatch):
    captured: dict[str, object] = {}
    doctor_calls = 0

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="smoke-custom-preflight")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="custom-smoke")

    def fake_doctor_report():
        nonlocal doctor_calls
        doctor_calls += 1
        return _doctor_report()

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", fake_doctor_report)
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    fake_pipeline = object()
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["smoke", "custom-smoke.yaml", "--preflight", "always"])

    assert result.exit_code == 0
    assert doctor_calls == 1
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "smoke-custom-preflight"
    assert captured["wait_timeout"] is None


def test_smoke_can_disable_bundled_preflight(monkeypatch):
    captured: dict[str, object] = {}

    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            captured["submitted_pipeline"] = pipeline
            return SimpleNamespace(id="smoke-no-preflight")

        async def wait(self, run_id: str, timeout: float | None = None):
            captured["wait_run_id"] = run_id
            captured["wait_timeout"] = timeout
            return _completed_run(run_id, pipeline_name="local-real-agents-kimi-smoke")

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: (_ for _ in ()).throw(AssertionError("doctor should not run")))
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    fake_pipeline = object()
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["smoke", "--preflight", "never"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "examples/local-real-agents-kimi-smoke.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "smoke-no-preflight"
    assert captured["wait_timeout"] is None


def test_smoke_supports_json_output(monkeypatch):
    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            return SimpleNamespace(id="smoke-json")

        async def wait(self, run_id: str, timeout: float | None = None):
            return _completed_run(run_id)

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: object())

    result = runner.invoke(app, ["smoke", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"id": "smoke-json", "status": "completed"}


def test_smoke_supports_json_summary_output(monkeypatch):
    class FakeOrchestrator:
        async def submit(self, pipeline: object):
            return SimpleNamespace(id="smoke-json-summary")

        async def wait(self, run_id: str, timeout: float | None = None):
            return _completed_run(
                run_id,
                pipeline_name="local-real-agents-kimi-smoke",
                pipeline_nodes=[
                    SimpleNamespace(
                        id="codex_plan",
                        agent=SimpleNamespace(value="codex"),
                        model=None,
                        provider=None,
                    )
                ],
                nodes={
                    "codex_plan": SimpleNamespace(
                        status=SimpleNamespace(value="completed"),
                        current_attempt=1,
                        attempts=[SimpleNamespace(number=1)],
                        exit_code=0,
                        final_response="codex ok",
                        output="codex ok",
                        stderr_lines=[],
                    )
                },
            )

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(
        agentflow.cli,
        "_build_runtime",
        lambda runs_dir, max_concurrent_runs: (SimpleNamespace(run_dir=lambda run_id: Path(runs_dir) / run_id), FakeOrchestrator()),
    )
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: object())

    result = runner.invoke(app, ["smoke", "--output", "json-summary"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "id": "smoke-json-summary",
        "status": "completed",
        "pipeline": {"name": "local-real-agents-kimi-smoke"},
        "started_at": "2026-03-08T04:11:03+00:00",
        "finished_at": "2026-03-08T04:11:10+00:00",
        "duration": "7.0s",
        "duration_seconds": 7.0,
        "run_dir": ".agentflow/runs/smoke-json-summary",
        "nodes": [
            {
                "id": "codex_plan",
                "status": "completed",
                "agent": "codex",
                "attempts": 1,
                "exit_code": 0,
                "preview": "codex ok",
            }
        ],
    }


def test_doctor_outputs_json_report(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())

    result = runner.invoke(app, ["doctor", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "status": "ok",
        "checks": [{"name": "kimi_shell_helper", "status": "ok", "detail": "ready"}],
    }


def test_doctor_defaults_to_json_report(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(agentflow.cli, "_stream_supports_tty_summary", lambda *, err: False)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "status": "ok",
        "checks": [{"name": "kimi_shell_helper", "status": "ok", "detail": "ready"}],
    }


def test_doctor_defaults_to_summary_report_on_tty(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(agentflow.cli, "_stream_supports_tty_summary", lambda *, err: True)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert result.stdout == "Doctor: ok\n- kimi_shell_helper: ok - ready\n"


def test_doctor_supports_summary_output(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())

    result = runner.invoke(app, ["doctor", "--output", "summary"])

    assert result.exit_code == 0
    assert result.stdout == "Doctor: ok\n- kimi_shell_helper: ok - ready\n"


def test_doctor_with_pipeline_path_augments_report_for_kimi_shell_bootstrap_warning(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                target=SimpleNamespace(kind="local", shell="bash", shell_init="kimi", shell_interactive=False),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: warning\n"
        "- kimi_shell_helper: ok - ready\n"
        "- kimi_shell_bootstrap: warning - Node `claude_review`: `shell_init: kimi` uses bash without interactive startup; helpers from `~/.bashrc` are usually unavailable. Set `target.shell_interactive: true` or use `bash -lic`.\n"
        "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.\n"
        "Pipeline auto preflight matches: claude_review (claude) via `target.shell_init`\n"
    )


def test_doctor_with_pipeline_path_fails_for_non_bash_kimi_shell_bootstrap(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider="kimi",
                env={},
                target=SimpleNamespace(kind="local", shell="sh", shell_init="kimi", shell_interactive=False),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 1
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: failed\n"
        "- kimi_shell_helper: ok - ready\n"
        "- kimi_shell_bootstrap: failed - Node `claude_review`: `shell_init: kimi` requires bash-style shell bootstrap, but `target.shell` resolves to `sh`. Use `shell: bash` with `target.shell_login: true` and `target.shell_interactive: true`, use `bash -lic`, or export provider variables directly.\n"
        "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.\n"
        "Pipeline auto preflight matches: claude_review (claude) via `target.shell_init`\n"
    )


def test_doctor_with_pipeline_path_augments_report_for_kimi_agent_bootstrap_warning(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="kimi_review",
                agent=SimpleNamespace(value="kimi"),
                env={"KIMI_API_KEY": "inline-secret"},
                target=SimpleNamespace(kind="local", shell="bash", shell_init="kimi", shell_interactive=False),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: warning\n"
        "- kimi_shell_helper: ok - ready\n"
        "- kimi_shell_bootstrap: warning - Node `kimi_review`: `shell_init: kimi` uses bash without interactive startup; helpers from `~/.bashrc` are usually unavailable. Set `target.shell_interactive: true` or use `bash -lic`.\n"
        "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.\n"
        "Pipeline auto preflight matches: kimi_review (kimi) via `target.shell_init`\n"
    )


def test_doctor_with_pipeline_path_fails_when_kimi_node_is_missing_kimi_api_key(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="kimi_review",
                agent=SimpleNamespace(value="kimi"),
                provider=None,
                env={},
                target=SimpleNamespace(kind="local"),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 1
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: failed\n"
        "- provider_credentials: failed - Node `kimi_review` (kimi) requires `KIMI_API_KEY` for provider `moonshot`, but it is not set in the current environment, `node.env`, or `provider.env`.\n"
        "Pipeline auto preflight: disabled - path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.\n"
    )


def test_doctor_with_pipeline_path_accepts_claude_kimi_provider_credentials_from_kimi_bootstrap(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(subprocess, "run", _completed_subprocess())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider="kimi",
                env={},
                target=SimpleNamespace(kind="local", shell="bash", shell_init="kimi", shell_interactive=True),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: ok\n"
        "- kimi_shell_helper: ok - ready\n"
        "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.\n"
        "Pipeline auto preflight matches: claude_review (claude) via `target.shell_init`\n"
    )


def test_doctor_with_pipeline_path_fails_when_local_claude_is_unavailable_after_shell_bootstrap(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(subprocess, "run", _completed_subprocess(returncode=1))
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider="kimi",
                env={},
                executable=None,
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 1
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: failed\n"
        "- kimi_shell_helper: ok - ready\n"
        "- claude_ready: failed - Node `claude_review` (claude) cannot launch local Claude after the node shell bootstrap; `claude --version` fails in the prepared local shell.\n"
        "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.\n"
        "Pipeline auto preflight matches: claude_review (claude) via `target.shell_init`\n"
    )


def test_doctor_with_pipeline_path_fails_when_local_codex_auth_is_unavailable(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def fake_run(*args, **kwargs):
        env = kwargs.get("env") or {}
        target_command = env.get("AGENTFLOW_TARGET_COMMAND", "")
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0 if target_command == "codex --version" else 1,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="codex_plan",
                agent=SimpleNamespace(value="codex"),
                provider=None,
                env={},
                executable=None,
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 1
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: failed\n"
        "- kimi_shell_helper: ok - ready\n"
        "- codex_auth: failed - Node `codex_plan` (codex) cannot authenticate local Codex after the node shell bootstrap; `codex login status` fails and `OPENAI_API_KEY` is not set in the current environment, `node.env`, or `provider.env`.\n"
        "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.\n"
        "Pipeline auto preflight matches: codex_plan (codex) via `target.shell_init`\n"
    )


def test_doctor_with_pipeline_path_accepts_local_codex_login_status_from_shell_bootstrap(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="codex_plan",
                agent=SimpleNamespace(value="codex"),
                provider=None,
                env={},
                executable=None,
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: ok\n"
        "- kimi_shell_helper: ok - ready\n"
        "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.\n"
        "Pipeline auto preflight matches: codex_plan (codex) via `target.shell_init`\n"
    )


def test_doctor_with_pipeline_path_accepts_provider_credentials_from_shell_init_export(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider="anthropic",
                env={},
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_init=["export ANTHROPIC_API_KEY=test-shell-key"],
                    shell_interactive=True,
                ),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: ok\n"
        "Pipeline auto preflight: disabled - path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.\n"
    )


def test_doctor_with_pipeline_path_accepts_provider_credentials_from_split_shell_init_export(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider="anthropic",
                env={},
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_init=["ANTHROPIC_API_KEY=test-shell-key", "export ANTHROPIC_API_KEY"],
                    shell_interactive=True,
                ),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: ok\n"
        "Pipeline auto preflight: disabled - path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.\n"
    )


def test_doctor_with_pipeline_path_accepts_custom_kimi_provider_credentials_from_shell_init_helper(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider=ProviderConfig(
                    name="kimi-proxy",
                    base_url="https://api.kimi.com/coding/",
                    api_key_env="ANTHROPIC_API_KEY",
                    env={},
                ),
                env={},
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_init="kimi",
                    shell_login=True,
                    shell_interactive=True,
                ),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: ok\n"
        "- kimi_shell_helper: ok - ready\n"
        "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.\n"
        "Pipeline auto preflight matches: claude_review (claude) via `target.shell_init`\n"
    )


def test_doctor_with_pipeline_path_accepts_custom_kimi_provider_env_base_url_credentials_from_shell_init_helper(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider=ProviderConfig(
                    name="kimi-proxy",
                    api_key_env="ANTHROPIC_API_KEY",
                    env={"ANTHROPIC_BASE_URL": "https://api.kimi.com/coding/"},
                ),
                env={},
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_init="kimi",
                    shell_login=True,
                    shell_interactive=True,
                ),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: ok\n"
        "- kimi_shell_helper: ok - ready\n"
        "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.\n"
        "Pipeline auto preflight matches: claude_review (claude) via `target.shell_init`\n"
    )


def test_doctor_with_pipeline_path_accepts_provider_credentials_from_shell_wrapper_export(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider="anthropic",
                env={},
                target=SimpleNamespace(
                    kind="local",
                    shell="bash -lc 'export ANTHROPIC_API_KEY=test-shell-key && {command}'",
                ),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: ok\n"
        "Pipeline auto preflight: disabled - path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.\n"
    )


def test_doctor_with_pipeline_path_accepts_provider_credentials_from_shell_prefix_env_wrapper(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider="anthropic",
                env={},
                target=SimpleNamespace(
                    kind="local",
                    shell="env ANTHROPIC_API_KEY=test-shell-key bash -c",
                ),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: ok\n"
        "Pipeline auto preflight: disabled - path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.\n"
    )


def test_doctor_with_pipeline_path_accepts_provider_credentials_from_split_shell_wrapper_export(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider="anthropic",
                env={},
                target=SimpleNamespace(
                    kind="local",
                    shell="bash -lc 'ANTHROPIC_API_KEY=test-shell-key && export ANTHROPIC_API_KEY && {command}'",
                ),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: ok\n"
        "Pipeline auto preflight: disabled - path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.\n"
    )


def test_doctor_with_pipeline_path_accepts_provider_credentials_from_login_shell_startup(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(subprocess, "run", _completed_subprocess())
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider="anthropic",
                env={},
                executable=None,
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: ok\n"
        "Pipeline auto preflight: disabled - path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.\n"
    )


def test_doctor_with_pipeline_path_accepts_provider_credentials_from_interactive_login_shell_startup(monkeypatch):
    captured: dict[str, object] = {}
    observed_commands: list[list[str]] = []

    def _run(*args, **kwargs):
        command = list(args[0])
        observed_commands.append(command)
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0 if command[:2] == ["bash", "-lic"] or command[:4] == ["bash", "-l", "-i", "-c"] else 1,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(subprocess, "run", _run)
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider="anthropic",
                env={},
                executable=None,
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert ["bash", "-lic", 'test -n "${ANTHROPIC_API_KEY:-}"'] in observed_commands
    assert result.stdout == (
        "Doctor: ok\n"
        "Pipeline auto preflight: disabled - path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.\n"
    )


def test_doctor_with_pipeline_path_accepts_provider_credentials_from_interactive_shell_startup(monkeypatch):
    captured: dict[str, object] = {}
    observed_commands: list[list[str]] = []

    def _run(*args, **kwargs):
        command = list(args[0])
        observed_commands.append(command)
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0 if command[:2] == ["bash", "-ic"] or command[:3] == ["bash", "-i", "-c"] else 1,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(subprocess, "run", _run)
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider="anthropic",
                env={},
                executable=None,
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_interactive=True,
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert ["bash", "-ic", 'test -n "${ANTHROPIC_API_KEY:-}"'] in observed_commands
    assert result.stdout == (
        "Doctor: ok\n"
        "Pipeline auto preflight: disabled - path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.\n"
    )


def test_doctor_with_pipeline_path_accepts_kimi_api_key_from_node_env(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(subprocess, "run", _completed_subprocess())
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="kimi_review",
                agent=SimpleNamespace(value="kimi"),
                provider=None,
                env={"KIMI_API_KEY": "inline-secret"},
                target=SimpleNamespace(kind="local"),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: ok\n"
        "Pipeline auto preflight: disabled - path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.\n"
    )


def test_doctor_with_pipeline_path_fails_when_local_kimi_bridge_is_unavailable(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(subprocess, "run", _completed_subprocess(returncode=1))
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="kimi_review",
                agent=SimpleNamespace(value="kimi"),
                provider=None,
                env={"KIMI_API_KEY": "inline-secret"},
                executable="python-kimi",
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "summary"])

    assert result.exit_code == 1
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert result.stdout == (
        "Doctor: failed\n"
        "- kimi_shell_helper: ok - ready\n"
        "- kimi_ready: failed - Node `kimi_review` (kimi) cannot launch the local Kimi bridge after the node shell bootstrap; `python-kimi -c 'import agentflow.remote.kimi_bridge'` fails in the prepared local shell.\n"
        "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.\n"
        "Pipeline auto preflight matches: kimi_review (kimi) via `target.shell_init`\n"
    )


def test_doctor_with_pipeline_path_supports_json_output(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    fake_pipeline = SimpleNamespace(nodes=[])
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "json"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert json.loads(result.stdout) == {
        "status": "ok",
        "checks": [],
        "pipeline": {
            "auto_preflight": {
                "enabled": False,
                "reason": "path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.",
                "matches": [],
                "match_summary": [],
            }
        },
    }


def test_doctor_with_pipeline_path_supports_json_summary_output(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    fake_pipeline = SimpleNamespace(nodes=[])
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "json-summary"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert json.loads(result.stdout) == {
        "status": "ok",
        "checks": [],
        "pipeline": {
            "auto_preflight": {
                "enabled": False,
                "reason": "path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.",
                "matches": [],
                "match_summary": [],
            }
        },
    }


def test_doctor_with_pipeline_path_reports_auto_preflight_metadata_in_json(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(subprocess, "run", _completed_subprocess())
    fake_pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="review",
                agent=SimpleNamespace(value="claude"),
                target=SimpleNamespace(kind="local", shell="bash", shell_init="kimi", shell_interactive=True),
            )
        ]
    )
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", _capture_pipeline_loader(captured, fake_pipeline))

    result = runner.invoke(app, ["doctor", "custom-smoke.yaml", "--output", "json"])

    assert result.exit_code == 0
    assert captured["loaded_path"] == "custom-smoke.yaml"
    assert json.loads(result.stdout) == {
        "status": "ok",
        "checks": [{"name": "kimi_shell_helper", "status": "ok", "detail": "ready"}],
        "pipeline": {
            "auto_preflight": {
                "enabled": True,
                "reason": "local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.",
                "matches": [
                    {
                        "node_id": "review",
                        "agent": "claude",
                        "trigger": "target.shell_init",
                    }
                ],
                "match_summary": ["review (claude) via `target.shell_init`"],
            }
        },
    }


def test_doctor_with_pipeline_path_reports_expected_launch_env_override_as_ok(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: doctor-base-url-override
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
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic")

    result = runner.invoke(app, ["doctor", str(pipeline_path), "--output", "summary"])

    assert result.exit_code == 0
    assert result.stdout.startswith(
        "Doctor: ok\n"
        "- kimi_shell_helper: ok - ready\n"
        "- launch_env_override: ok - Node `review`: Launch env uses configured `ANTHROPIC_BASE_URL` value `https://api.kimi.com/coding/` instead of current `https://open.bigmodel.cn/api/anthropic` via `provider.base_url`.\n"
    )


def test_doctor_with_pipeline_path_json_includes_launch_env_override_context(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: doctor-base-url-override-json
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
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic")

    result = runner.invoke(app, ["doctor", str(pipeline_path), "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["checks"] == [
        {"name": "kimi_shell_helper", "status": "ok", "detail": "ready"},
        {
            "name": "launch_env_override",
            "status": "ok",
            "detail": "Node `review`: Launch env uses configured `ANTHROPIC_BASE_URL` value `https://api.kimi.com/coding/` instead of current `https://open.bigmodel.cn/api/anthropic` via `provider.base_url`.",
            "context": {
                "node_id": "review",
                "key": "ANTHROPIC_BASE_URL",
                "current_value": "https://open.bigmodel.cn/api/anthropic",
                "launch_value": "https://api.kimi.com/coding/",
                "source": "provider.base_url",
            },
        },
    ]


def test_doctor_without_path_reports_bundled_smoke_override_as_ok(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "bundled-smoke.yaml"
    pipeline_path.write_text(
        """name: bundled-smoke
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
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: str(pipeline_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic")

    result = runner.invoke(app, ["doctor", "--output", "summary"])

    assert result.exit_code == 0
    assert result.stdout == (
        "Doctor: ok\n"
        "- kimi_shell_helper: ok - ready\n"
        "- launch_env_override: ok - Node `review`: Launch env uses configured `ANTHROPIC_BASE_URL` value `https://api.kimi.com/coding/` instead of current `https://open.bigmodel.cn/api/anthropic` via `provider.base_url`.\n"
    )


def test_doctor_without_path_applies_bundled_pipeline_shell_checks(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "bundled-smoke.yaml"
    pipeline_path.write_text(
        """name: bundled-smoke
working_dir: .
nodes:
  - id: review
    agent: claude
    provider: kimi
    prompt: hi
    target:
      kind: local
      shell: sh
      shell_init: kimi
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="ok",
            checks=[DoctorCheck(name="kimi_shell_helper", status="ok", detail="ready")],
        ),
    )
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: str(pipeline_path))

    result = runner.invoke(app, ["doctor", "--output", "json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "status": "failed",
        "checks": [
            {"name": "kimi_shell_helper", "status": "ok", "detail": "ready"},
            {
                "name": "kimi_shell_bootstrap",
                "status": "failed",
                "detail": "Node `review`: `shell_init: kimi` requires bash-style shell bootstrap, but `target.shell` resolves to `sh`. Use `shell: bash` with `target.shell_login: true` and `target.shell_interactive: true`, use `bash -lic`, or export provider variables directly.",
            },
        ],
    }


def test_doctor_can_include_shell_bridge_in_json_output(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(
        agentflow.cli,
        "build_bash_login_shell_bridge_recommendation",
        lambda: ShellBridgeRecommendation(
            target="~/.bash_profile",
            source="~/.profile",
            snippet='if [ -f "$HOME/.profile" ]; then\n  . "$HOME/.profile"\nfi\n',
            reason="Bash login shells use `~/.bash_profile`, so `~/.profile` never runs.",
        ),
    )

    result = runner.invoke(app, ["doctor", "--output", "json", "--shell-bridge"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "status": "ok",
        "checks": [{"name": "kimi_shell_helper", "status": "ok", "detail": "ready"}],
        "shell_bridge": {
            "target": "~/.bash_profile",
            "source": "~/.profile",
            "snippet": 'if [ -f "$HOME/.profile" ]; then\n  . "$HOME/.profile"\nfi\n',
            "reason": "Bash login shells use `~/.bash_profile`, so `~/.profile` never runs.",
        },
    }


def test_doctor_can_include_shell_bridge_in_summary_output(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(
        agentflow.cli,
        "build_bash_login_shell_bridge_recommendation",
        lambda: ShellBridgeRecommendation(
            target="~/.bash_profile",
            source="~/.profile",
            snippet='if [ -f "$HOME/.profile" ]; then\n  . "$HOME/.profile"\nfi\n',
            reason="Bash login shells use `~/.bash_profile`, so `~/.profile` never runs.",
        ),
    )

    result = runner.invoke(app, ["doctor", "--output", "summary", "--shell-bridge"])

    assert result.exit_code == 0
    assert result.stdout == (
        "Doctor: ok\n"
        "- kimi_shell_helper: ok - ready\n"
        "Shell bridge suggestion for `~/.bash_profile` from `~/.profile`:\n"
        "Reason: Bash login shells use `~/.bash_profile`, so `~/.profile` never runs.\n"
        "if [ -f \"$HOME/.profile\" ]; then\n"
        "  . \"$HOME/.profile\"\n"
        "fi\n"
    )


def test_doctor_shell_bridge_summary_reports_when_no_fix_is_needed(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())
    monkeypatch.setattr(agentflow.cli, "build_bash_login_shell_bridge_recommendation", lambda: None)

    result = runner.invoke(app, ["doctor", "--output", "summary", "--shell-bridge"])

    assert result.exit_code == 0
    assert result.stdout == "Doctor: ok\n- kimi_shell_helper: ok - ready\nShell bridge suggestion: not needed\n"


def test_doctor_command_does_not_import_web_stack(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    script = """
import builtins
import importlib
import json
import os

from typer.testing import CliRunner
from agentflow.doctor import DoctorCheck, DoctorReport

os.environ.pop("ANTHROPIC_BASE_URL", None)

original_import = builtins.__import__

def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name in {\"agentflow.app\", \"fastapi\"}:
        raise ModuleNotFoundError(name)
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = fake_import
cli_module = importlib.import_module(\"agentflow.cli\")
cli_module.build_local_smoke_doctor_report = lambda: DoctorReport(
    status=\"ok\",
    checks=[DoctorCheck(name=\"kimi_shell_helper\", status=\"ok\", detail=\"ready\")],
)
result = CliRunner().invoke(cli_module.app, [\"doctor\"])
print(json.dumps({\"exit_code\": result.exit_code, \"stdout\": result.stdout}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        cwd=repo_root,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "exit_code": 0,
        "stdout": json.dumps({
        "status": "ok",
        "checks": [{"name": "kimi_shell_helper", "status": "ok", "detail": "ready"}],
    }, indent=2) + "\n",
    }


def test_run_command_executes_local_kimi_node_when_pipeline_lives_outside_repo(tmp_path, monkeypatch):
    pipeline_path = tmp_path / "kimi-only.yaml"
    pipeline_path.write_text(
        """name: kimi-only
working_dir: .
nodes:
  - id: review
    agent: kimi
    prompt: |
      Reply with exactly: kimi ok
    timeout_seconds: 30
    success_criteria:
      - kind: output_contains
        value: kimi ok
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTFLOW_KIMI_MOCK_RESPONSE", "kimi ok")

    result = runner.invoke(app, ["run", str(pipeline_path), "--output", "summary"])

    assert result.exit_code == 0
    assert "Run " in result.stdout
    assert "Pipeline: kimi-only" in result.stdout
    assert "review [kimi]: completed" in result.stdout
    assert "kimi ok" in result.stdout


def test_smoke_stops_when_bundled_preflight_fails(monkeypatch):
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report(status="failed", detail="missing"))
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: (_ for _ in ()).throw(AssertionError("pipeline should not load")))

    result = runner.invoke(app, ["smoke"])

    assert result.exit_code == 1
    assert result.stdout == "Doctor: failed\n- kimi_shell_helper: failed - missing\n"


def test_smoke_failed_preflight_includes_shell_bridge_when_available(monkeypatch):
    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="failed",
            checks=[
                DoctorCheck(name="bash_login_startup", status="warning", detail="missing bridge"),
                DoctorCheck(name="kimi_shell_helper", status="failed", detail="missing"),
            ],
        ),
    )
    monkeypatch.setattr(agentflow.cli, "build_bash_login_shell_bridge_recommendation", _shell_bridge_recommendation)
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: (_ for _ in ()).throw(AssertionError("pipeline should not load")))

    result = runner.invoke(app, ["smoke"])

    assert result.exit_code == 1
    assert result.stdout == (
        "Doctor: failed\n"
        "- bash_login_startup: warning - missing bridge\n"
        "- kimi_shell_helper: failed - missing\n"
        "Shell bridge suggestion for `~/.bash_profile` from `~/.profile`:\n"
        "Reason: Bash login shells use `~/.bash_profile`, so `~/.profile` never runs.\n"
        "if [ -f \"$HOME/.profile\" ]; then\n"
        "  . \"$HOME/.profile\"\n"
        "fi\n"
    )


def test_smoke_failed_preflight_honors_json_output(monkeypatch):
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report(status="failed", detail="missing"))
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: (_ for _ in ()).throw(AssertionError("pipeline should not load")))

    result = runner.invoke(app, ["smoke", "--output", "json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "status": "failed",
        "checks": [{"name": "kimi_shell_helper", "status": "failed", "detail": "missing"}],
    }


def test_smoke_failed_preflight_includes_shell_bridge_json_when_available(monkeypatch):
    monkeypatch.setattr(
        agentflow.cli,
        "build_local_smoke_doctor_report",
        lambda: DoctorReport(
            status="failed",
            checks=[
                DoctorCheck(name="bash_login_startup", status="warning", detail="missing bridge"),
                DoctorCheck(name="kimi_shell_helper", status="failed", detail="missing"),
            ],
        ),
    )
    monkeypatch.setattr(agentflow.cli, "build_bash_login_shell_bridge_recommendation", _shell_bridge_recommendation)
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: (_ for _ in ()).throw(AssertionError("pipeline should not load")))

    result = runner.invoke(app, ["smoke", "--output", "json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "status": "failed",
        "checks": [
            {"name": "bash_login_startup", "status": "warning", "detail": "missing bridge"},
            {"name": "kimi_shell_helper", "status": "failed", "detail": "missing"},
        ],
        "shell_bridge": _shell_bridge_recommendation().as_dict(),
    }


def test_smoke_failed_preflight_honors_json_summary_output(monkeypatch):
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report(status="failed", detail="missing"))
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
    monkeypatch.setattr(agentflow.cli, "_load_pipeline", lambda path: (_ for _ in ()).throw(AssertionError("pipeline should not load")))

    result = runner.invoke(app, ["smoke", "--output", "json-summary"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "status": "failed",
        "checks": [{"name": "kimi_shell_helper", "status": "failed", "detail": "missing"}],
    }
