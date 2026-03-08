from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from typer.testing import CliRunner

import agentflow.cli
from agentflow.cli import app
from agentflow.doctor import DoctorCheck, DoctorReport, ShellBridgeRecommendation

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
    assert "Auto preflight: enabled - local Codex/Claude nodes use a `kimi` shell bootstrap." in result.stdout
    assert "Auto preflight matches: plan (codex) via `target.shell_init`" in result.stdout
    assert "Note: Dependency references use placeholder node outputs" in result.stdout
    assert "- plan [codex/local]" in result.stdout
    assert "Model: gpt-5" in result.stdout
    assert "Mode: tools=read_only, capture=final" in result.stdout
    assert "Bootstrap: shell=bash, login=true, interactive=true, init=kimi" in result.stdout
    assert "Launch: bash -l -i -c 'kimi && eval \"$AGENTFLOW_TARGET_COMMAND\"'" in result.stdout
    assert "Runtime files: codex_home/config.toml" in result.stdout
    assert "Prompt: Review this: <inspect placeholder for nodes.plan.output>" in result.stdout


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

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--node", "review", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"]["name"] == "inspect-json"
    assert payload["pipeline"]["auto_preflight"] == {
        "enabled": True,
        "reason": "local Codex/Claude nodes use a `kimi` shell bootstrap.",
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

    result = runner.invoke(app, ["inspect", str(pipeline_path), "--node", "review", "--output", "json-summary"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pipeline"] == {
        "name": "inspect-json-summary",
        "working_dir": str(tmp_path.resolve()),
        "node_count": 1,
        "auto_preflight": "enabled - local Codex/Claude nodes use a `kimi` shell bootstrap.",
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
            "bootstrap": "shell=bash, login=true, interactive=true, init=kimi",
            "prompt_preview": "Reply with exactly: claude ok",
            "prepared_command": "claude -p 'Reply with exactly: claude ok' --output-format stream-json --verbose --permission-mode bypassPermissions --tools Read,Glob,Grep,LS,NotebookRead,Task,TaskOutput,TodoRead,WebFetch,WebSearch",
            "launch": "bash -l -i -c 'kimi && eval \"$AGENTFLOW_TARGET_COMMAND\"'",
            "cwd": str(tmp_path.resolve()),
            "env_keys": ["AGENTFLOW_TARGET_COMMAND", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"],
        }
    ]


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
        "disabled - path does not match the bundled smoke pipeline and no local Codex/Claude node uses `kimi` bootstrap."
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
    payload = json.loads(result.stderr)
    assert payload["status"] == "warning"
    assert payload["checks"] == [
        {"name": "kimi_shell_helper", "status": "ok", "detail": "ready"},
        {
            "name": "kimi_shell_bootstrap",
            "status": "warning",
            "detail": "Node `review`: `shell_init: kimi` uses bash without interactive startup; helpers from `~/.bashrc` are usually unavailable. Set `target.shell_interactive: true` or use `bash -lic`.",
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
    assert result.stderr == "Doctor: warning\n- claude: warning - bootstrap-only\n"
    assert captured["loaded_path"] == "examples/local-real-agents-kimi-smoke.yaml"
    assert captured["submitted_pipeline"] is fake_pipeline
    assert captured["wait_run_id"] == "smoke-warning"
    assert captured["wait_timeout"] is None


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
    assert result.stderr == "Doctor: warning\n- claude: warning - bootstrap-only\n"
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
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())

    result = runner.invoke(app, ["doctor", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "status": "ok",
        "checks": [{"name": "kimi_shell_helper", "status": "ok", "detail": "ready"}],
    }


def test_doctor_defaults_to_json_report(monkeypatch):
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "status": "ok",
        "checks": [{"name": "kimi_shell_helper", "status": "ok", "detail": "ready"}],
    }


def test_doctor_supports_summary_output(monkeypatch):
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report())

    result = runner.invoke(app, ["doctor", "--output", "summary"])

    assert result.exit_code == 0
    assert result.stdout == "Doctor: ok\n- kimi_shell_helper: ok - ready\n"


def test_doctor_can_include_shell_bridge_in_json_output(monkeypatch):
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

from typer.testing import CliRunner
from agentflow.doctor import DoctorCheck, DoctorReport

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


def test_smoke_stops_when_bundled_preflight_fails(monkeypatch):
    monkeypatch.setattr(agentflow.cli, "build_local_smoke_doctor_report", lambda: _doctor_report(status="failed", detail="missing"))
    monkeypatch.setattr(agentflow.cli, "default_smoke_pipeline_path", lambda: "examples/local-real-agents-kimi-smoke.yaml")
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
