from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.runners.container import ContainerRunner
from agentflow.runners.local import LocalRunner
from agentflow.specs import LocalTarget, NodeSpec, PipelineSpec


def _paths(tmp_path: Path) -> ExecutionPaths:
    runtime_dir = tmp_path / ".runtime"
    return ExecutionPaths(
        host_workdir=tmp_path,
        host_runtime_dir=runtime_dir,
        target_workdir=str(tmp_path),
        target_runtime_dir=str(runtime_dir),
        app_root=tmp_path,
    )


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for_pid_exit(pid: int, *, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while _pid_exists(pid):
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"PID {pid} did not exit within {timeout}s")
        await asyncio.sleep(0.05)


def _parent_exits_but_descendant_holds_pipe_command(pid_file: Path, *, sleep_seconds: int) -> list[str]:
    child_code = (
        "from pathlib import Path; import os, time; "
        f"Path({str(pid_file)!r}).write_text(str(os.getpid()), encoding='utf-8'); "
        'print("ready", flush=True); '
        f"time.sleep({sleep_seconds})"
    )
    parent_code = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}], "
        "stdout=sys.stdout, stderr=sys.stderr, close_fds=False)"
    )
    return [sys.executable, "-c", parent_code]


def _parent_exits_but_descendant_finishes_command(message: str) -> list[str]:
    child_code = f'print({message!r}, flush=True)'
    parent_code = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}], "
        "stdout=sys.stdout, stderr=sys.stderr, close_fds=False)"
    )
    return [sys.executable, "-c", parent_code]


@pytest.mark.asyncio
async def test_local_runner_uses_configured_shell(tmp_path: Path):
    shell_env = tmp_path / "shell.env"
    shell_env.write_text("myagent(){ printf 'shell wrapper ok\\n'; }\n", encoding="utf-8")

    node = NodeSpec.model_validate(
        {
            "id": "alpha",
            "agent": "codex",
            "prompt": "hi",
            "target": {"kind": "local", "shell": f"env BASH_ENV={shell_env} bash -c"},
        }
    )
    prepared = PreparedExecution(
        command=["myagent"],
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code == 0
    assert result.stdout_lines == ["shell wrapper ok"]
    assert result.stderr_lines == []


@pytest.mark.asyncio
async def test_local_runner_creates_missing_workdir(tmp_path: Path):
    workdir = tmp_path / "agents" / "agent_007"

    node = NodeSpec.model_validate(
        {
            "id": "alpha-workdir",
            "agent": "codex",
            "prompt": "hi",
        }
    )
    prepared = PreparedExecution(
        command=["bash", "-lc", "pwd"],
        env={},
        cwd=str(workdir),
        trace_kind="codex",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code == 0
    assert workdir.is_dir()
    assert result.stdout_lines == [str(workdir)]
    assert result.stderr_lines == []


@pytest.mark.asyncio
async def test_local_runner_supports_exec_prefixed_shell_wrapper(tmp_path: Path):
    shell_env = tmp_path / "shell.env"
    shell_env.write_text("myagent(){ printf 'exec wrapper ok\\n'; }\n", encoding="utf-8")

    node = NodeSpec.model_validate(
        {
            "id": "alpha-exec",
            "agent": "codex",
            "prompt": "hi",
            "target": {"kind": "local", "shell": f"exec env BASH_ENV={shell_env} bash -c"},
        }
    )
    prepared = PreparedExecution(
        command=["myagent"],
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code == 0
    assert result.stdout_lines == ["exec wrapper ok"]
    assert result.stderr_lines == []


@pytest.mark.asyncio
async def test_local_runner_shell_template_bootstraps_command(tmp_path: Path):
    shell_env = tmp_path / "shell.env"
    shell_env.write_text("kimi(){ export WRAPPED_VALUE='template ok'; }\n", encoding="utf-8")

    node = NodeSpec.model_validate(
        {
            "id": "beta",
            "agent": "codex",
            "prompt": "hi",
            "target": {
                "kind": "local",
                "shell": f"env BASH_ENV={shell_env} bash -c 'kimi; {{command}}'",
            },
        }
    )
    prepared = PreparedExecution(
        command=["bash", "-lc", 'printf "%s" "$WRAPPED_VALUE"'],
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code == 0
    assert result.stdout_lines == ["template ok"]
    assert result.stderr_lines == []


@pytest.mark.asyncio
async def test_local_runner_shell_template_without_explicit_command_flag_defaults_to_c(tmp_path: Path):
    shell_env = tmp_path / "shell.env"
    shell_env.write_text("myagent(){ printf 'template default ok\\n'; }\n", encoding="utf-8")

    node = NodeSpec.model_validate(
        {
            "id": "beta-default-c",
            "agent": "codex",
            "prompt": "hi",
            "target": {
                "kind": "local",
                "shell": f"env BASH_ENV={shell_env} bash {{command}}",
            },
        }
    )
    prepared = PreparedExecution(
        command=["myagent"],
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code == 0
    assert result.stdout_lines == ["template default ok"]
    assert result.stderr_lines == []


@pytest.mark.asyncio
async def test_local_runner_shell_init_runs_in_login_interactive_shell(tmp_path: Path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".hushlogin").write_text("", encoding="utf-8")
    (fake_home / ".profile").write_text(
        'if [ -f "$HOME/.bashrc" ]; then\n  . "$HOME/.bashrc"\nfi\n',
        encoding="utf-8",
    )
    (fake_home / ".bashrc").write_text(
        "case $- in\n"
        "  *i*) ;;\n"
        "  *) return;;\n"
        "esac\n"
        "kimi(){ export WRAPPED_VALUE=interactive-ok; }\n",
        encoding="utf-8",
    )

    node = NodeSpec.model_validate(
        {
            "id": "gamma",
            "agent": "claude",
            "prompt": "hi",
            "target": {
                "kind": "local",
                "shell": "bash",
                "shell_login": True,
                "shell_interactive": True,
                "shell_init": "kimi",
            },
        }
    )
    prepared = PreparedExecution(
        command=["bash", "-lc", 'printf "%s" "$WRAPPED_VALUE"'],
        env={"HOME": str(fake_home)},
        cwd=str(tmp_path),
        trace_kind="claude",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code == 0
    assert result.stdout_lines[-1] == "interactive-ok"
    assert result.stderr_lines == []


@pytest.mark.asyncio
async def test_local_runner_shell_init_adds_interactive_flag_after_env_wrapper_options(tmp_path: Path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".hushlogin").write_text("", encoding="utf-8")
    (fake_home / ".profile").write_text(
        'if [ -f "$HOME/.bashrc" ]; then\n  . "$HOME/.bashrc"\nfi\n',
        encoding="utf-8",
    )
    (fake_home / ".bashrc").write_text(
        "case $- in\n"
        "  *i*) ;;\n"
        "  *) return;;\n"
        "esac\n"
        "kimi(){ export WRAPPED_VALUE=wrapped-interactive-ok; }\n",
        encoding="utf-8",
    )

    node = NodeSpec.model_validate(
        {
            "id": "gamma-env-wrapper",
            "agent": "claude",
            "prompt": "hi",
            "target": {
                "kind": "local",
                "shell": f"env -i HOME={fake_home} PATH={os.environ.get('PATH', '/usr/bin:/bin')} bash",
                "shell_login": True,
                "shell_interactive": True,
                "shell_init": "kimi",
            },
        }
    )
    prepared = PreparedExecution(
        command=["python3", "-c", 'import os; print(os.getenv("WRAPPED_VALUE", ""))'],
        env={},
        cwd=str(tmp_path),
        trace_kind="claude",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code == 0
    assert result.stdout_lines[-1] == "wrapped-interactive-ok"
    assert result.stderr_lines == []


@pytest.mark.asyncio
async def test_local_runner_env_wrapper_preserves_launch_env_when_clearing_environment(tmp_path: Path):
    node = NodeSpec.model_validate(
        {
            "id": "gamma-env-wrapper-launch-env",
            "agent": "codex",
            "prompt": "hi",
            "target": {
                "kind": "local",
                "shell": f"env -i PATH={os.environ.get('PATH', '/usr/bin:/bin')} bash",
            },
        }
    )
    prepared = PreparedExecution(
        command=["python3", "-c", 'import os; print(os.getenv("OPENAI_API_KEY", "missing"))'],
        env={"OPENAI_API_KEY": "node-secret"},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code == 0
    assert result.stdout_lines == ["node-secret"]
    assert result.stderr_lines == []


@pytest.mark.asyncio
async def test_local_runner_inherited_kimi_bootstrap_defaults_run_in_login_interactive_shell(tmp_path: Path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".hushlogin").write_text("", encoding="utf-8")
    (fake_home / ".profile").write_text(
        'if [ -f "$HOME/.bashrc" ]; then\n  . "$HOME/.bashrc"\nfi\n',
        encoding="utf-8",
    )
    (fake_home / ".bashrc").write_text(
        "case $- in\n"
        "  *i*) ;;\n"
        "  *) return;;\n"
        "esac\n"
        "kimi(){ export WRAPPED_VALUE=inherited-kimi-ok; }\n",
        encoding="utf-8",
    )

    pipeline = PipelineSpec.model_validate(
        {
            "name": "inherited-kimi-bootstrap",
            "working_dir": str(tmp_path),
            "local_target_defaults": {"bootstrap": "kimi"},
            "nodes": [
                {
                    "id": "gamma-inherited-bootstrap",
                    "agent": "claude",
                    "prompt": "hi",
                }
            ],
        }
    )
    node = pipeline.nodes[0]
    prepared = PreparedExecution(
        command=["bash", "-lc", 'printf "%s" "$WRAPPED_VALUE"'],
        env={"HOME": str(fake_home)},
        cwd=str(tmp_path),
        trace_kind="claude",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code == 0
    assert result.stdout_lines[-1] == "inherited-kimi-ok"
    assert result.stderr_lines == []


@pytest.mark.asyncio
async def test_local_runner_shell_init_list_runs_commands_in_order(tmp_path: Path):
    shell_env = tmp_path / "shell.env"
    shell_env.write_text(
        "prepare(){ export SHELL_INIT_STEP=ordered; }\n"
        "kimi(){ export WRAPPED_VALUE=${SHELL_INIT_STEP}-ok; }\n",
        encoding="utf-8",
    )

    node = NodeSpec.model_validate(
        {
            "id": "gamma-list",
            "agent": "claude",
            "prompt": "hi",
            "target": {
                "kind": "local",
                "shell": f"env BASH_ENV={shell_env} bash -c",
                "shell_init": ["prepare", "kimi"],
            },
        }
    )
    prepared = PreparedExecution(
        command=["bash", "-lc", 'printf "%s" "$WRAPPED_VALUE"'],
        env={},
        cwd=str(tmp_path),
        trace_kind="claude",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code == 0
    assert result.stdout_lines[-1] == "ordered-ok"
    assert result.stderr_lines == []


@pytest.mark.asyncio
async def test_local_runner_explicit_bash_lic_suppresses_job_control_noise(tmp_path: Path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".hushlogin").write_text("", encoding="utf-8")
    (fake_home / ".profile").write_text(
        """if [ -f "$HOME/.bashrc" ]; then
  . "$HOME/.bashrc"
fi
""",
        encoding="utf-8",
    )
    (fake_home / ".bashrc").write_text(
        """case $- in
  *i*) ;;
  *) return;;
esac
export WRAPPED_VALUE=explicit-lic-ok
""",
        encoding="utf-8",
    )

    node = NodeSpec.model_validate(
        {
            "id": "gamma-explicit-shell",
            "agent": "claude",
            "prompt": "hi",
            "target": {
                "kind": "local",
                "shell": "bash -lic",
            },
        }
    )
    prepared = PreparedExecution(
        command=["python3", "-c", 'import os; print(os.getenv("WRAPPED_VALUE", ""), end="")'],
        env={"HOME": str(fake_home)},
        cwd=str(tmp_path),
        trace_kind="claude",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code == 0
    assert result.stdout_lines[-1] == "explicit-lic-ok"
    assert result.stderr_lines == []


@pytest.mark.asyncio
async def test_local_runner_suppresses_initialize_job_control_noise_for_interactive_bash(tmp_path: Path):
    node = NodeSpec.model_validate(
        {
            "id": "gamma-init-job-control-noise",
            "agent": "claude",
            "prompt": "hi",
            "target": {
                "kind": "local",
                "shell": "bash",
                "shell_interactive": True,
            },
        }
    )
    prepared = PreparedExecution(
        command=[
            "python3",
            "-c",
            (
                'import sys; '
                'sys.stderr.write("bash: initialize_job_control: no job control in background: Bad file descriptor\\n"); '
                'print("interactive-ok", end="")'
            ),
        ],
        env={},
        cwd=str(tmp_path),
        trace_kind="claude",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code == 0
    assert result.stdout_lines == ["interactive-ok"]
    assert result.stderr_lines == []


@pytest.mark.asyncio
async def test_local_runner_shell_init_failure_stops_wrapped_command(tmp_path: Path):
    node = NodeSpec.model_validate(
        {
            "id": "gamma-fail",
            "agent": "claude",
            "prompt": "hi",
            "target": {
                "kind": "local",
                "shell": "bash",
                "shell_init": "missing_helper",
            },
        }
    )
    prepared = PreparedExecution(
        command=["python3", "-c", 'print("wrapped command should not run", end="")'],
        env={},
        cwd=str(tmp_path),
        trace_kind="claude",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code != 0
    assert result.stdout_lines == []
    assert result.stderr_lines == ["bash: line 1: missing_helper: command not found"]


def test_local_runner_rejects_inline_shell_command_payload_without_placeholder(tmp_path: Path):
    node = NodeSpec.model_validate(
        {
            "id": "inline-command-payload",
            "agent": "codex",
            "prompt": "hi",
            "target": {"kind": "local", "shell": "bash"},
        }
    )
    node.target = LocalTarget.model_construct(kind="local", shell="bash -lc 'echo pre'")
    prepared = PreparedExecution(
        command=["python3", "-c", 'print("wrapped", end="")'],
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    with pytest.raises(ValueError, match=r"shell command payload.*\{command\}"):
        LocalRunner().plan_execution(node, prepared, _paths(tmp_path))


@pytest.mark.asyncio
async def test_local_runner_plain_shell_does_not_enable_login_mode(tmp_path: Path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".profile").write_text("export WRAPPED_VALUE=from-profile\n", encoding="utf-8")

    node = NodeSpec.model_validate(
        {
            "id": "delta",
            "agent": "codex",
            "prompt": "hi",
            "target": {
                "kind": "local",
                "shell": "bash",
            },
        }
    )
    prepared = PreparedExecution(
        command=["python3", "-c", "import os; print(os.getenv('WRAPPED_VALUE', 'missing'), end='')"],
        env={"HOME": str(fake_home)},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code == 0
    assert result.stdout_lines == ["missing"]
    assert result.stderr_lines == []


@pytest.mark.asyncio
async def test_local_runner_empty_env_value_clears_inherited_host_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example/v1")

    node = NodeSpec.model_validate(
        {
            "id": "delta-clear-env",
            "agent": "codex",
            "prompt": "hi",
        }
    )
    prepared = PreparedExecution(
        command=[
            "python3",
            "-c",
            'import json, os; print(json.dumps(os.getenv("OPENAI_BASE_URL")))',
        ],
        env={"OPENAI_BASE_URL": ""},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.exit_code == 0
    assert result.stdout_lines == ['""']
    assert result.stderr_lines == []


@pytest.mark.asyncio
async def test_local_runner_cancellation_escalates_when_process_ignores_sigterm(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(LocalRunner, "_TERMINATE_GRACE_SECONDS", 0.1)

    node = NodeSpec.model_validate(
        {
            "id": "cancel-ignores-sigterm",
            "agent": "codex",
            "prompt": "hi",
        }
    )
    prepared = PreparedExecution(
        command=[
            "python3",
            "-c",
            (
                "import signal, time; "
                "signal.signal(signal.SIGTERM, lambda signum, frame: None); "
                'print("ready", flush=True); '
                "time.sleep(60)"
            ),
        ],
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    cancel_requested = False

    async def request_cancel() -> None:
        nonlocal cancel_requested
        await asyncio.sleep(0.2)
        cancel_requested = True

    cancel_task = asyncio.create_task(request_cancel())
    try:
        result = await asyncio.wait_for(
            LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: cancel_requested),
            timeout=2,
        )
    finally:
        await cancel_task

    assert result.cancelled is True
    assert result.timed_out is False
    assert result.exit_code == 130
    assert result.stdout_lines == ["ready"]
    assert result.stderr_lines == ["Cancelled by user"]


@pytest.mark.asyncio
async def test_local_runner_timeout_uses_standard_exit_code(tmp_path: Path):
    node = NodeSpec.model_validate(
        {
            "id": "timeout-standard-exit",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 1,
        }
    )
    prepared = PreparedExecution(
        command=[
            "python3",
            "-c",
            'import time; print("ready", flush=True); time.sleep(60)',
        ],
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.cancelled is False
    assert result.timed_out is True
    assert result.exit_code == 124
    assert result.stdout_lines == ["ready"]
    assert result.stderr_lines == ["Timed out after 1s"]


@pytest.mark.asyncio
async def test_local_runner_timeout_kills_descendant_after_parent_exit(tmp_path: Path):
    pid_file = tmp_path / "descendant.pid"
    node = NodeSpec.model_validate(
        {
            "id": "timeout-descendant-after-parent-exit",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 1,
        }
    )
    prepared = PreparedExecution(
        command=_parent_exits_but_descendant_holds_pipe_command(pid_file, sleep_seconds=60),
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    descendant_pid: int | None = None
    try:
        result = await asyncio.wait_for(
            LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False),
            timeout=3,
        )
    finally:
        if pid_file.exists():
            descendant_pid = int(pid_file.read_text(encoding="utf-8"))
            if _pid_exists(descendant_pid):
                os.kill(descendant_pid, signal.SIGKILL)
                await _wait_for_pid_exit(descendant_pid)

    assert result.cancelled is False
    assert result.timed_out is True
    assert result.exit_code == 124
    assert result.stdout_lines == ["ready"]
    assert result.stderr_lines == ["Timed out after 1s"]
    assert descendant_pid is not None
    assert _pid_exists(descendant_pid) is False


@pytest.mark.asyncio
async def test_local_runner_waits_for_descendant_output_after_parent_exit(tmp_path: Path):
    node = NodeSpec.model_validate(
        {
            "id": "descendant-output-after-parent-exit",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 5,
        }
    )
    prepared = PreparedExecution(
        command=_parent_exits_but_descendant_finishes_command("descendant-ok"),
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False)

    assert result.cancelled is False
    assert result.timed_out is False
    assert result.exit_code == 0
    assert result.stdout_lines == ["descendant-ok"]
    assert result.stderr_lines == []


def test_local_runner_plan_execution_includes_shell_wrapper(tmp_path: Path):
    node = NodeSpec.model_validate(
        {
            "id": "plan-local",
            "agent": "claude",
            "prompt": "hi",
            "target": {
                "kind": "local",
                "shell": "bash",
                "shell_login": True,
                "shell_interactive": True,
                "shell_init": "kimi",
            },
        }
    )
    prepared = PreparedExecution(
        command=["claude", "-p", "hello world"],
        env={"ANTHROPIC_BASE_URL": "https://example.test"},
        cwd=str(tmp_path),
        trace_kind="claude",
        runtime_files={"claude-mcp.json": "{}"},
    )

    plan = LocalRunner().plan_execution(node, prepared, _paths(tmp_path))

    assert plan.kind == "process"
    assert plan.command == ["bash", "-l", "-i", "-c", 'kimi && eval "$AGENTFLOW_TARGET_COMMAND"']
    assert plan.cwd == str(tmp_path)
    assert plan.runtime_files == ["claude-mcp.json"]
    assert plan.env == {
        "ANTHROPIC_BASE_URL": "https://example.test",
        "AGENTFLOW_TARGET_COMMAND": "claude -p 'hello world'",
    }


def test_local_runner_plan_execution_kimi_cli(tmp_path: Path):
    node = NodeSpec.model_validate(
        {
            "id": "plan-local-kimi",
            "agent": "kimi",
            "prompt": "hi",
        }
    )
    prepared = PreparedExecution(
        command=["kimi", "--print", "--output-format", "stream-json", "--yolo", "-p", "hi"],
        env={},
        cwd=str(tmp_path),
        trace_kind="kimi",
    )

    plan = LocalRunner().plan_execution(node, prepared, _paths(tmp_path))

    assert plan.command == ["kimi", "--print", "--output-format", "stream-json", "--yolo", "-p", "hi"]
    assert plan.env == {}


def test_container_runner_plan_execution_shows_host_and_container_context(tmp_path: Path):
    node = NodeSpec.model_validate(
        {
            "id": "plan-container",
            "agent": "codex",
            "prompt": "hi",
            "target": {
                "kind": "container",
                "image": "ghcr.io/example/agentflow:test",
                "extra_args": ["--network", "host"],
            },
        }
    )
    prepared = PreparedExecution(
        command=["codex", "exec", "ping"],
        env={"OPENAI_API_KEY": "secret"},
        cwd="/workspace/task",
        trace_kind="codex",
        runtime_files={"codex_home/config.toml": "model = 'gpt-5'\n"},
    )

    plan = ContainerRunner().plan_execution(node, prepared, _paths(tmp_path))

    assert plan.kind == "container"
    assert plan.command[:6] == [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{tmp_path}:/workspace",
        "-v",
    ]
    assert plan.cwd == str(tmp_path)
    assert plan.runtime_files == ["codex_home/config.toml"]
    assert plan.payload == {
        "image": "ghcr.io/example/agentflow:test",
        "engine": "docker",
        "workdir": "/workspace/task",
        "env": {"OPENAI_API_KEY": "secret"},
    }


async def _noop_output(stream_name: str, text: str) -> None:
    return None
