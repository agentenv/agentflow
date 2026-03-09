from __future__ import annotations

import asyncio
import os
import shlex
from contextlib import suppress

from agentflow.local_shell import render_shell_init, shell_wrapper_requires_command_placeholder, target_uses_interactive_bash
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.runners.base import LaunchPlan, RawExecutionResult, Runner, StreamCallback
from agentflow.specs import LocalTarget, NodeSpec


class LocalRunner(Runner):
    _INTERACTIVE_SHELL_STDERR_NOISE = (
        "bash: cannot set terminal process group (",
        "bash: initialize_job_control: no job control in background:",
        "bash: no job control in this shell",
    )
    _SHELL_COMMAND_PLACEHOLDER_MESSAGE = (
        "`target.shell` already includes a shell command payload. Add `{command}` where AgentFlow should inject "
        "the prepared agent command."
    )

    def _has_flag(self, shell_parts: list[str], short_flag: str, long_flag: str | None = None) -> bool:
        return any(
            part == long_flag or (part.startswith("-") and not part.startswith("--") and short_flag in part[1:])
            for part in shell_parts[1:]
        )

    def _command_flag_index(self, shell_parts: list[str]) -> int | None:
        for index, part in enumerate(shell_parts[1:], start=1):
            if part == "--command" or (part.startswith("-") and not part.startswith("--") and "c" in part[1:]):
                return index
        return None

    def _apply_shell_options(self, shell_parts: list[str], target: LocalTarget) -> list[str]:
        updated = list(shell_parts)
        command_index = self._command_flag_index(updated)
        insert_at = command_index if command_index is not None else len(updated)
        if target.shell_login and not self._has_flag(updated, "l", "--login"):
            updated.insert(insert_at, "-l")
            insert_at += 1
        if target.shell_interactive and not self._has_flag(updated, "i"):
            updated.insert(insert_at, "-i")
        return updated

    def _replace_shell_template_command(self, shell_parts: list[str], placeholder: str, shell_command: str) -> list[str]:
        return [part.replace(placeholder, shell_command) for part in shell_parts]

    def _augment_local_env(self, prepared: PreparedExecution, paths: ExecutionPaths) -> dict[str, str]:
        env = dict(prepared.env)
        if prepared.command[1:3] != ["-m", "agentflow.remote.kimi_bridge"]:
            return env

        app_root = str(paths.app_root)
        pythonpath = env.get("PYTHONPATH") or os.environ.get("PYTHONPATH")
        if pythonpath:
            entries = [entry for entry in pythonpath.split(os.pathsep) if entry]
            if app_root not in entries:
                env["PYTHONPATH"] = os.pathsep.join([app_root, *entries])
            return env

        env["PYTHONPATH"] = app_root
        return env

    def _command_for_target(self, node: NodeSpec, prepared: PreparedExecution) -> tuple[list[str], dict[str, str]]:
        target = node.target
        if not isinstance(target, LocalTarget) or not target.shell:
            return prepared.command, {}
        if shell_wrapper_requires_command_placeholder(target.shell):
            raise ValueError(self._SHELL_COMMAND_PLACEHOLDER_MESSAGE)

        command_text = shlex.join(prepared.command)
        shell_command = 'eval "$AGENTFLOW_TARGET_COMMAND"'
        shell_init = render_shell_init(target.shell_init)
        if shell_init:
            shell_command = f"{shell_init} && {shell_command}"

        if "{command}" in target.shell:
            placeholder = "__AGENTFLOW_COMMAND_PLACEHOLDER__"
            shell_parts = shlex.split(target.shell.replace("{command}", placeholder))
            if not shell_parts:
                return prepared.command, {}
            shell_parts = self._apply_shell_options(shell_parts, target)
            command_index = self._command_flag_index(shell_parts)
            if command_index is None:
                placeholder_index = next(
                    (index for index, part in enumerate(shell_parts) if placeholder in part),
                    None,
                )
                if placeholder_index is not None:
                    shell_parts.insert(placeholder_index, "-c")
            shell_parts = self._replace_shell_template_command(shell_parts, placeholder, shell_command)
            return shell_parts, {"AGENTFLOW_TARGET_COMMAND": command_text}

        shell_parts = self._apply_shell_options(shlex.split(target.shell), target)
        if not shell_parts:
            return prepared.command, {}

        command_index = self._command_flag_index(shell_parts)
        if command_index is None:
            shell_parts.append("-c")

        if shell_init:
            shell_parts.append(shell_command)
            return shell_parts, {"AGENTFLOW_TARGET_COMMAND": command_text}

        return [*shell_parts, command_text], {}

    def plan_execution(
        self,
        node: NodeSpec,
        prepared: PreparedExecution,
        paths: ExecutionPaths,
    ) -> LaunchPlan:
        command, target_env = self._command_for_target(node, prepared)
        plan_env = self._augment_local_env(prepared, paths)
        plan_env.update(target_env)
        return LaunchPlan(
            command=command,
            env=plan_env,
            cwd=prepared.cwd,
            stdin=prepared.stdin,
            runtime_files=sorted(prepared.runtime_files),
        )

    def _should_suppress_stderr(self, node: NodeSpec, text: str) -> bool:
        if not target_uses_interactive_bash(node.target):
            return False
        return any(text.startswith(prefix) for prefix in self._INTERACTIVE_SHELL_STDERR_NOISE)

    async def _consume_stream(self, node: NodeSpec, stream, stream_name: str, buffer: list[str], on_output: StreamCallback) -> None:
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            if stream_name == "stderr" and self._should_suppress_stderr(node, text):
                continue
            buffer.append(text)
            await on_output(stream_name, text)

    async def execute(
        self,
        node: NodeSpec,
        prepared: PreparedExecution,
        paths: ExecutionPaths,
        on_output: StreamCallback,
        should_cancel,
    ) -> RawExecutionResult:
        self.materialize_runtime_files(paths.host_runtime_dir, prepared.runtime_files)
        env = os.environ.copy()
        env.update(self._augment_local_env(prepared, paths))
        command, target_env = self._command_for_target(node, prepared)
        env.update(target_env)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=prepared.cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if prepared.stdin is not None else None,
        )
        if prepared.stdin is not None and process.stdin is not None:
            process.stdin.write(prepared.stdin.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        stdout_task = asyncio.create_task(self._consume_stream(node, process.stdout, "stdout", stdout_lines, on_output))
        stderr_task = asyncio.create_task(self._consume_stream(node, process.stderr, "stderr", stderr_lines, on_output))
        wait_task = asyncio.create_task(process.wait())
        deadline = asyncio.get_running_loop().time() + node.timeout_seconds
        timed_out = False
        cancelled = False

        try:
            while not wait_task.done():
                if should_cancel():
                    cancelled = True
                    process.terminate()
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    timed_out = True
                    process.kill()
                    break
                await asyncio.sleep(0.1)
            await wait_task
        finally:
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            if timed_out:
                stderr_lines.append(f"Timed out after {node.timeout_seconds}s")
                await on_output("stderr", stderr_lines[-1])
            if cancelled:
                stderr_lines.append("Cancelled by user")
                await on_output("stderr", stderr_lines[-1])
            with suppress(ProcessLookupError):
                if not wait_task.done():
                    process.kill()
                    await wait_task

        exit_code = process.returncode if process.returncode is not None else (130 if cancelled else 124)
        return RawExecutionResult(
            exit_code=exit_code,
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines,
            timed_out=timed_out,
            cancelled=cancelled,
        )
