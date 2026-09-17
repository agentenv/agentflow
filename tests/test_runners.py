from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import textwrap
import time

import pytest

from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.runners.container import ContainerRunner
from agentflow.runners.local import LocalRunner
from agentflow.specs import AgentKind, LocalTarget, NodeSpec, PipelineSpec
from agentflow.traces import create_trace_parser


def _paths(tmp_path: Path) -> ExecutionPaths:
    runtime_dir = tmp_path / ".runtime"
    return ExecutionPaths(
        host_workdir=tmp_path,
        host_runtime_dir=runtime_dir,
        target_workdir=str(tmp_path),
        target_runtime_dir=str(runtime_dir),
        app_root=tmp_path,
    )


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


def _terminating_descendant_script(
    ready_path: Path,
    terminated_path: Path,
) -> str:
    return textwrap.dedent(
        f"""
        import os
        import signal
        import time
        from pathlib import Path

        ready_path = Path({str(ready_path)!r})
        terminated_path = Path({str(terminated_path)!r})

        def terminate(_signum, _frame):
            terminated_path.write_text("terminated", encoding="utf-8")
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, terminate)
        ready_path.write_text(str(os.getpgrp()), encoding="utf-8")
        while True:
            time.sleep(1)
        """
    )


async def _assert_process_gone(pid: int, message: str) -> None:
    deadline = time.monotonic() + 2
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        if time.monotonic() >= deadline:
            pytest.fail(message)
        await asyncio.sleep(0.05)


def _codex_session_script(
    *,
    terminal_event: str,
    final_message: str | None = None,
    stdout_terminal_delay_seconds: float | None = None,
    stdout_final_item_delay_seconds: float | None = None,
    stdout_exit_code: int | None = None,
    descendant_pid_path: Path | None = None,
    descendant_ready_path: Path | None = None,
    descendant_terminated_path: Path | None = None,
) -> str:
    payload = {
        "type": terminal_event,
        "turn_id": "01a0afad-39d7-7ef3-a74c-7b14def5de2f",
    }
    if final_message is not None:
        payload["last_agent_message"] = final_message
    descendant_setup = ""
    if (
        descendant_pid_path is not None
        and descendant_ready_path is not None
        and descendant_terminated_path is not None
    ):
        child_script = _terminating_descendant_script(
            descendant_ready_path,
            descendant_terminated_path,
        )
        descendant_setup = textwrap.dedent(
            f"""
            descendant = subprocess.Popen([sys.executable, "-c", {child_script!r}])
            Path({str(descendant_pid_path)!r}).write_text(str(descendant.pid), encoding="utf-8")
            ready_path = Path({str(descendant_ready_path)!r})
            deadline = time.monotonic() + 5
            while not ready_path.exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("descendant did not start")
                time.sleep(0.01)
            """
        )
    indented_descendant_setup = textwrap.indent(descendant_setup, "        ")
    delayed_stdout = ""
    if stdout_terminal_delay_seconds is not None:
        final_stdout = ""
        if stdout_exit_code is not None and final_message is not None:
            final_stdout = (
                f'print(json.dumps({{"type": "item.completed", "item": '
                f'{{"type": "agent_message", "text": {final_message!r}}}}}), flush=True)'
            )
        exit_stdout = (
            f"raise SystemExit({stdout_exit_code})"
            if stdout_exit_code is not None
            else ""
        )
        delayed_stdout = textwrap.dedent(
            f"""
            time.sleep({stdout_terminal_delay_seconds!r})
            {final_stdout}
            print(json.dumps({{"type": "turn.completed"}}), flush=True)
            {exit_stdout}
            """
        )
    indented_delayed_stdout = textwrap.indent(delayed_stdout, "        ")
    delayed_final_item_stdout = ""
    if stdout_final_item_delay_seconds is not None:
        if final_message is None:
            raise ValueError("final_message is required for delayed final item stdout")
        delayed_final_item_stdout = textwrap.dedent(
            f"""
            time.sleep({stdout_final_item_delay_seconds!r})
            print(json.dumps({{"type": "item.completed", "item": {{
                "type": "agent_message",
                "text": {final_message!r},
            }}}}), flush=True)
            """
        )
    indented_delayed_final_item_stdout = textwrap.indent(
        delayed_final_item_stdout,
        "        ",
    )
    return textwrap.dedent(
        f"""
        import json
        import os
        import subprocess
        import sys
        import time
        from datetime import datetime, timezone
        from pathlib import Path

{indented_descendant_setup}
        thread_id = "01a0afad-399c-71c1-b944-8421ddfa839a"
        turn_id = "01a0afad-39d7-7ef3-a74c-7b14def5de2f"
        session_dir = Path(os.environ["CODEX_HOME"]) / "sessions" / "2026" / "09" / "17"
        session_dir.mkdir(parents=True, exist_ok=True)
        session_path = session_dir / f"rollout-{{thread_id}}.jsonl"
        print(json.dumps({{"type": "thread.started", "thread_id": thread_id}}), flush=True)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        records = [
            {{"timestamp": now, "type": "event_msg", "payload": {{"type": "task_started", "turn_id": turn_id}}}},
            {{"timestamp": now, "type": "event_msg", "payload": {payload!r}}},
        ]
        with session_path.open("w", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record) + "\\n")
                stream.flush()
{indented_delayed_stdout}
{indented_delayed_final_item_stdout}
        time.sleep(60)
        """
    )


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups are required")
async def test_local_runner_recovers_completed_codex_session_when_cli_stalls(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(LocalRunner, "_CODEX_COMPLETION_STALL_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(LocalRunner, "_EXTERNAL_COMPLETION_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(LocalRunner, "_TERMINATE_GRACE_SECONDS", 0.1)
    codex_home = tmp_path / "codex-home"
    descendant_pid_path = tmp_path / "descendant.pid"
    descendant_ready_path = tmp_path / "descendant.ready"
    descendant_terminated_path = tmp_path / "descendant.terminated"
    node = NodeSpec.model_validate(
        {
            "id": "codex-completed-session-stall",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 30,
        }
    )
    prepared = PreparedExecution(
        command=[
            sys.executable,
            "-c",
            _codex_session_script(
                terminal_event="task_complete",
                final_message="Review complete.",
                descendant_pid_path=descendant_pid_path,
                descendant_ready_path=descendant_ready_path,
                descendant_terminated_path=descendant_terminated_path,
            ),
        ],
        env={"CODEX_HOME": str(codex_home)},
        cwd=str(tmp_path),
        trace_kind="codex",
    )
    output: list[tuple[str, str]] = []

    async def on_output(stream: str, line: str) -> None:
        output.append((stream, line))

    result = await asyncio.wait_for(
        LocalRunner().execute(node, prepared, _paths(tmp_path), on_output, lambda: False),
        timeout=3,
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    recovered = [json.loads(line) for stream, line in output if stream == "stdout"]
    assert recovered[-2] == {
        "type": "item.completed",
        "item": {
            "id": "agentflow_recovered_final",
            "type": "agent_message",
            "text": "Review complete.",
        },
        "recovered_from": "codex_session",
    }
    assert recovered[-1] == {
        "type": "turn.completed",
        "recovered_from": "codex_session",
    }
    descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
    assert int(descendant_ready_path.read_text(encoding="utf-8")) != os.getpgrp()
    assert descendant_terminated_path.read_text(encoding="utf-8") == "terminated"
    await _assert_process_gone(
        descendant_pid,
        "Codex descendant survived recovered completion",
    )


@pytest.mark.asyncio
async def test_local_runner_does_not_duplicate_terminal_stdout_arriving_during_grace(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(LocalRunner, "_CODEX_COMPLETION_STALL_GRACE_SECONDS", 1.0)
    monkeypatch.setattr(LocalRunner, "_EXTERNAL_COMPLETION_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(LocalRunner, "_TERMINATE_GRACE_SECONDS", 0.1)
    codex_home = tmp_path / "codex-home"
    node = NodeSpec.model_validate(
        {
            "id": "codex-delayed-terminal-stdout",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 30,
        }
    )
    prepared = PreparedExecution(
        command=[
            sys.executable,
            "-c",
            _codex_session_script(
                terminal_event="task_complete",
                final_message="Review complete.",
                stdout_terminal_delay_seconds=0.6,
            ),
        ],
        env={"CODEX_HOME": str(codex_home)},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await asyncio.wait_for(
        LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False),
        timeout=4,
    )

    stdout_events = [json.loads(line) for line in result.stdout_lines]
    assert result.exit_code == 0
    assert result.timed_out is False
    assert [event["type"] for event in stdout_events] == [
        "thread.started",
        "turn.completed",
    ]
    assert all("recovered_from" not in event for event in stdout_events)


@pytest.mark.asyncio
async def test_local_runner_recovers_only_terminal_after_final_item_stdout(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(LocalRunner, "_CODEX_COMPLETION_STALL_GRACE_SECONDS", 0.5)
    monkeypatch.setattr(LocalRunner, "_EXTERNAL_COMPLETION_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(LocalRunner, "_TERMINATE_GRACE_SECONDS", 0.1)
    codex_home = tmp_path / "codex-home"
    node = NodeSpec.model_validate(
        {
            "id": "codex-partial-final-stdout",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 30,
        }
    )
    prepared = PreparedExecution(
        command=[
            sys.executable,
            "-c",
            _codex_session_script(
                terminal_event="task_complete",
                final_message="done",
                stdout_final_item_delay_seconds=0.1,
            ),
        ],
        env={"CODEX_HOME": str(codex_home)},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await asyncio.wait_for(
        LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False),
        timeout=4,
    )

    stdout_events = [json.loads(line) for line in result.stdout_lines]
    parser = create_trace_parser(AgentKind.CODEX, node.id)
    for line in result.stdout_lines:
        parser.feed(line)
    assert result.exit_code == 0
    assert result.timed_out is False
    assert [event["type"] for event in stdout_events] == [
        "thread.started",
        "item.completed",
        "turn.completed",
    ]
    assert stdout_events[-1]["recovered_from"] == "codex_session"
    assert parser.finalize() == "done"


@pytest.mark.asyncio
async def test_local_runner_prefers_real_exit_during_external_completion_grace(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(LocalRunner, "_CODEX_COMPLETION_STALL_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(LocalRunner, "_EXTERNAL_COMPLETION_GRACE_SECONDS", 1.0)
    monkeypatch.setattr(LocalRunner, "_TERMINATE_GRACE_SECONDS", 0.1)
    codex_home = tmp_path / "codex-home"
    node = NodeSpec.model_validate(
        {
            "id": "codex-real-exit-during-external-grace",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 30,
        }
    )
    prepared = PreparedExecution(
        command=[
            sys.executable,
            "-c",
            _codex_session_script(
                terminal_event="task_complete",
                final_message="Review complete.",
                stdout_terminal_delay_seconds=0.6,
                stdout_exit_code=23,
            ),
        ],
        env={"CODEX_HOME": str(codex_home)},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await asyncio.wait_for(
        LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False),
        timeout=4,
    )

    stdout_events = [json.loads(line) for line in result.stdout_lines]
    assert result.exit_code == 23
    assert result.timed_out is False
    assert [event["type"] for event in stdout_events] == [
        "thread.started",
        "item.completed",
        "turn.completed",
    ]
    assert all("recovered_from" not in event for event in stdout_events)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups are required")
async def test_local_runner_prefers_returncode_while_exit_waiter_is_pending(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(LocalRunner, "_EXTERNAL_COMPLETION_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(LocalRunner, "_TERMINATE_GRACE_SECONDS", 0.2)
    ready_path = tmp_path / "ready"

    class NearDeadlineExitRunner(LocalRunner):
        def __init__(self) -> None:
            super().__init__()
            self.saw_pending_waiter_after_exit = False

        async def _wait_for_ready(self) -> int:
            while not ready_path.exists():
                await asyncio.sleep(0.001)
            return 0

        def _external_completion(self, node, prepared, paths):
            return self._wait_for_ready()

        async def _wait_for_returncode(self, process) -> int:
            while process.returncode is None:
                await asyncio.sleep(0.001)
            await asyncio.sleep(0.15)
            return process.returncode

        async def _terminate_with_fallback(
            self,
            process,
            wait_task,
            process_group_id=None,
        ) -> None:
            self.saw_pending_waiter_after_exit = (
                process.returncode == 23 and not wait_task.done()
            )
            await super()._terminate_with_fallback(
                process,
                wait_task,
                process_group_id,
            )

    script = textwrap.dedent(
        f"""
        import time
        from pathlib import Path

        Path({str(ready_path)!r}).write_text("ready", encoding="utf-8")
        time.sleep(0.08)
        raise SystemExit(23)
        """
    )
    node = NodeSpec.model_validate(
        {
            "id": "codex-returncode-before-waiter",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 30,
        }
    )
    prepared = PreparedExecution(
        command=[sys.executable, "-c", script],
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
    )
    runner = NearDeadlineExitRunner()

    result = await asyncio.wait_for(
        runner.execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False),
        timeout=3,
    )

    assert runner.saw_pending_waiter_after_exit is True
    assert result.exit_code == 23
    assert result.timed_out is False


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups are required")
async def test_local_runner_preserves_codex_exit_code_and_cleans_inherited_descendant(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(LocalRunner, "_TERMINATE_GRACE_SECONDS", 0.1)
    descendant_pid_path = tmp_path / "normal-descendant.pid"
    descendant_ready_path = tmp_path / "normal-descendant.ready"
    descendant_terminated_path = tmp_path / "normal-descendant.terminated"
    child_script = _terminating_descendant_script(
        descendant_ready_path,
        descendant_terminated_path,
    )
    parent_script = textwrap.dedent(
        f"""
        import subprocess
        import sys
        import time
        from pathlib import Path

        descendant = subprocess.Popen([sys.executable, "-c", {child_script!r}])
        Path({str(descendant_pid_path)!r}).write_text(str(descendant.pid), encoding="utf-8")
        ready_path = Path({str(descendant_ready_path)!r})
        deadline = time.monotonic() + 5
        while not ready_path.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("descendant did not start")
            time.sleep(0.01)
        raise SystemExit(23)
        """
    )
    node = NodeSpec.model_validate(
        {
            "id": "codex-normal-exit-with-descendant",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 30,
        }
    )
    prepared = PreparedExecution(
        command=[sys.executable, "-c", parent_script],
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await asyncio.wait_for(
        LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False),
        timeout=3,
    )

    descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
    assert result.exit_code == 23
    assert result.timed_out is False
    assert int(descendant_ready_path.read_text(encoding="utf-8")) != os.getpgrp()
    assert descendant_terminated_path.read_text(encoding="utf-8") == "terminated"
    await _assert_process_gone(
        descendant_pid,
        "Codex descendant survived normal root exit",
    )


@pytest.mark.asyncio
async def test_local_runner_fails_closed_when_stalled_codex_session_is_aborted(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(LocalRunner, "_CODEX_COMPLETION_STALL_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(LocalRunner, "_EXTERNAL_COMPLETION_GRACE_SECONDS", 0.05)
    codex_home = tmp_path / "codex-home"
    node = NodeSpec.model_validate(
        {
            "id": "codex-aborted-session-stall",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 30,
        }
    )
    prepared = PreparedExecution(
        command=[sys.executable, "-c", _codex_session_script(terminal_event="turn_aborted")],
        env={"CODEX_HOME": str(codex_home)},
        cwd=str(tmp_path),
        trace_kind="codex",
    )
    output: list[tuple[str, str]] = []

    async def on_output(stream: str, line: str) -> None:
        output.append((stream, line))

    result = await asyncio.wait_for(
        LocalRunner().execute(node, prepared, _paths(tmp_path), on_output, lambda: False),
        timeout=3,
    )

    assert result.exit_code == 1
    assert result.timed_out is False
    assert json.loads(output[-1][1]) == {
        "type": "turn.failed",
        "recovered_from": "codex_session",
    }


@pytest.mark.asyncio
async def test_local_runner_ignores_completed_turn_from_prior_codex_invocation(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(LocalRunner, "_TERMINATE_GRACE_SECONDS", 0.05)
    codex_home = tmp_path / "codex-home"
    session_dir = codex_home / "sessions" / "2026" / "09" / "17"
    session_dir.mkdir(parents=True)
    thread_id = "01a0afad-399c-71c1-b944-8421ddfa839a"
    session_path = session_dir / f"rollout-{thread_id}.jsonl"
    old_records = [
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "prior-turn"},
        },
        {
            "timestamp": "2026-01-01T00:00:01Z",
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": "prior-turn"},
        },
    ]
    session_path.write_text(
        "".join(json.dumps(record) + "\n" for record in old_records),
        encoding="utf-8",
    )
    script = (
        "import json, time; "
        f"print(json.dumps({{'type':'thread.started','thread_id':'{thread_id}'}}), flush=True); "
        "time.sleep(60)"
    )
    node = NodeSpec.model_validate(
        {
            "id": "codex-prior-completion",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 1,
        }
    )
    prepared = PreparedExecution(
        command=[sys.executable, "-c", script],
        env={"CODEX_HOME": str(codex_home)},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await asyncio.wait_for(
        LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False),
        timeout=3,
    )

    assert result.exit_code == 124
    assert result.timed_out is True


@pytest.mark.asyncio
async def test_codex_completion_monitor_uses_launch_boundary_and_latest_retry(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(LocalRunner, "_CODEX_COMPLETION_STALL_GRACE_SECONDS", 0.15)
    monkeypatch.setattr(LocalRunner, "_EXTERNAL_COMPLETION_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(LocalRunner, "_TERMINATE_GRACE_SECONDS", 0.1)
    codex_home = tmp_path / "codex-home"
    session_dir = codex_home / "sessions" / "2026" / "09" / "17"
    session_dir.mkdir(parents=True)
    thread_id = "01a0afad-399c-71c1-b944-8421ddfa839a"
    session_path = session_dir / f"rollout-{thread_id}.jsonl"
    prior_turn = "01a0afad-1111-7111-a111-111111111111"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    prior_records = [
        {
            "timestamp": now,
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": prior_turn},
        },
        {
            "timestamp": now,
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": prior_turn,
                "last_agent_message": "prior invocation",
            },
        },
    ]
    session_path.write_text(
        "".join(json.dumps(record) + "\n" for record in prior_records),
        encoding="utf-8",
    )
    first_turn = "01a0afad-2222-7222-a222-222222222222"
    retry_turn = "01a0afad-3333-7333-a333-333333333333"
    script = textwrap.dedent(
        f"""
        import json
        import time
        from datetime import datetime, timezone
        from pathlib import Path

        path = Path({str(session_path)!r})
        print(json.dumps({{"type": "thread.started", "thread_id": {thread_id!r}}}), flush=True)

        def append(payload):
            record = {{
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "type": "event_msg",
                "payload": payload,
            }}
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\\n")

        append({{"type": "task_started", "turn_id": {first_turn!r}}})
        append({{
            "type": "task_complete",
            "turn_id": {first_turn!r},
            "last_agent_message": "superseded attempt",
        }})
        time.sleep(0.1)
        append({{"type": "task_started", "turn_id": {retry_turn!r}}})
        time.sleep(0.05)
        append({{
            "type": "task_complete",
            "turn_id": {retry_turn!r},
            "last_agent_message": "latest retry",
        }})
        time.sleep(60)
        """
    )
    node = NodeSpec.model_validate(
        {
            "id": "codex-resume-retry-race",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 30,
        }
    )
    prepared = PreparedExecution(
        command=[sys.executable, "-c", script],
        env={"CODEX_HOME": str(codex_home)},
        cwd=str(tmp_path),
        trace_kind="codex",
    )
    output: list[tuple[str, str]] = []

    async def on_output(stream: str, line: str) -> None:
        output.append((stream, line))

    result = await asyncio.wait_for(
        LocalRunner().execute(node, prepared, _paths(tmp_path), on_output, lambda: False),
        timeout=3,
    )

    recovered = [
        json.loads(line)
        for stream, line in output
        if stream == "stdout" and json.loads(line).get("type") == "item.completed"
    ]
    assert result.exit_code == 0
    assert recovered[-1]["item"]["text"] == "latest retry"


@pytest.mark.asyncio
async def test_local_runner_termination_closes_subprocess_transport():
    class FakeTransport:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FakeProcess:
        def __init__(self) -> None:
            self._transport = FakeTransport()
            self.terminated = False
            self.killed = False

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    process = FakeProcess()
    wait_task = asyncio.create_task(asyncio.sleep(0, result=0))

    await LocalRunner()._terminate_with_fallback(process, wait_task)

    assert process.terminated is True
    assert process.killed is False
    assert process._transport.closed is True


@pytest.mark.asyncio
async def test_local_runner_stdin_none_does_not_inherit_outer_pipe(tmp_path: Path):
    outer_script = textwrap.dedent(
        """
import asyncio
import json
import sys
from pathlib import Path

from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.runners.local import LocalRunner
from agentflow.specs import NodeSpec

async def _noop_output(stream_name: str, text: str) -> None:
    return None

async def main() -> None:
    workdir = Path(sys.argv[1])
    runtime_dir = workdir / ".runtime"
    node = NodeSpec.model_validate(
        {
            "id": "stdin-inherit-repro",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 1,
        }
    )
    prepared = PreparedExecution(
        command=[
            "python3",
            "-c",
            'import sys; print("child-start", flush=True); sys.stdin.read(); print("child-done", flush=True)',
        ],
        env={},
        cwd=str(workdir),
        trace_kind="codex",
        stdin=None,
    )
    paths = ExecutionPaths(
        host_workdir=workdir,
        host_runtime_dir=runtime_dir,
        target_workdir=str(workdir),
        target_runtime_dir=str(runtime_dir),
        app_root=Path.cwd(),
    )
    result = await LocalRunner().execute(node, prepared, paths, _noop_output, lambda: False)
    print(
        json.dumps(
            {
                "exit_code": result.exit_code,
                "stdout_lines": result.stdout_lines,
                "stderr_lines": result.stderr_lines,
                "timed_out": result.timed_out,
            }
        ),
        flush=True,
    )

asyncio.run(main())
"""
    )
    repo_root = Path(__file__).resolve().parents[1]
    outer = await asyncio.create_subprocess_exec(
        "python3",
        "-c",
        outer_script,
        str(tmp_path),
        cwd=str(repo_root),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    assert outer.stdin is not None
    await asyncio.wait_for(outer.wait(), timeout=5)

    stdout = await outer.stdout.read()
    stderr = await outer.stderr.read()
    outer.stdin.close()

    assert outer.returncode == 0
    assert stderr.decode("utf-8") == ""

    payload = json.loads(stdout.decode("utf-8"))
    assert payload["exit_code"] == 0
    assert payload["stdout_lines"] == ["child-start", "child-done"]
    assert payload["timed_out"] is False
    assert payload["stderr_lines"] == []


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


@pytest.mark.asyncio
async def test_container_runner_execute_inherits_local_stdin_handling(tmp_path: Path, monkeypatch):
    node = NodeSpec.model_validate(
        {
            "id": "container-stdin-devnull",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 5,
            "target": {"kind": "container", "image": "ghcr.io/example/agentflow:test"},
        }
    )
    prepared = PreparedExecution(
        command=["ignored"],
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
        stdin=None,
    )

    def _container_prepared(_self, _node: NodeSpec, _prepared: PreparedExecution, _paths: ExecutionPaths) -> PreparedExecution:
        return PreparedExecution(
            command=[
                "python3",
                "-c",
                "import sys; print('stdin-start', flush=True); sys.stdin.read(); print('stdin-end', flush=True)",
            ],
            env={},
            cwd=str(tmp_path),
            trace_kind=_prepared.trace_kind,
            stdin=None,
        )

    monkeypatch.setattr(ContainerRunner, "_container_prepared", _container_prepared)

    result = await asyncio.wait_for(
        ContainerRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False),
        timeout=3,
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.cancelled is False
    assert result.stdout_lines == ["stdin-start", "stdin-end"]


@pytest.mark.asyncio
async def test_local_runner_detects_silent_process_exit(tmp_path: Path):
    """Process that exits with code 0 and produces no output should complete promptly."""
    node = NodeSpec.model_validate(
        {
            "id": "silent-exit",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 5,
        }
    )
    prepared = PreparedExecution(
        command=["python3", "-c", "pass"],
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await asyncio.wait_for(
        LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False),
        timeout=3,
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.cancelled is False
    assert result.stdout_lines == []


@pytest.mark.asyncio
async def test_local_runner_timeout_kills_hanging_process(tmp_path: Path):
    """Process that ignores SIGTERM is killed after timeout + grace period."""
    node = NodeSpec.model_validate(
        {
            "id": "timeout-kill",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 1,
        }
    )
    prepared = PreparedExecution(
        command=[
            "python3",
            "-c",
            (
                "import signal, time; "
                "signal.signal(signal.SIGTERM, lambda s, f: None); "
                'print("started", flush=True); '
                "time.sleep(120)"
            ),
        ],
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await asyncio.wait_for(
        LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False),
        timeout=15,
    )

    assert result.timed_out is True
    assert result.exit_code == 124
    assert result.stdout_lines == ["started"]
    assert any("Timed out" in line for line in result.stderr_lines)


@pytest.mark.asyncio
async def test_local_runner_process_crash_detected_promptly(tmp_path: Path):
    """Process that crashes (non-zero exit) should be detected without waiting for timeout."""
    node = NodeSpec.model_validate(
        {
            "id": "crash-detect",
            "agent": "codex",
            "prompt": "hi",
            "timeout_seconds": 30,
        }
    )
    prepared = PreparedExecution(
        command=[
            "python3",
            "-c",
            'print("before crash", flush=True); raise SystemExit(1)',
        ],
        env={},
        cwd=str(tmp_path),
        trace_kind="codex",
    )

    result = await asyncio.wait_for(
        LocalRunner().execute(node, prepared, _paths(tmp_path), _noop_output, lambda: False),
        timeout=5,
    )

    assert result.exit_code == 1
    assert result.timed_out is False
    assert result.stdout_lines == ["before crash"]


async def _noop_output(stream_name: str, text: str) -> None:
    return None
