from __future__ import annotations

import asyncio
import codecs
import os
import shlex
import signal
from pathlib import Path
from contextlib import suppress

from agentflow.local_shell import render_shell_init, shell_wrapper_requires_command_placeholder, target_uses_interactive_bash
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.runners.base import LaunchPlan, RawExecutionResult, Runner, StreamCallback
from agentflow.specs import LocalTarget, NodeSpec
from agentflow.utils import ensure_dir


class LocalRunner(Runner):
    _KNOWN_SHELL_EXECUTABLES = {
        "ash",
        "bash",
        "dash",
        "fish",
        "ksh",
        "mksh",
        "pwsh",
        "sh",
        "zsh",
    }
    _SHELL_BUILTIN_PREFIX_TOKENS = {"exec"}
    _INTERACTIVE_SHELL_STDERR_NOISE = (
        "bash: cannot set terminal process group (",
        "bash: initialize_job_control: no job control in background:",
        "bash: no job control in this shell",
    )
    _STREAM_READ_SIZE = 65536
    _TERMINATE_GRACE_SECONDS = 1.0
    _SHELL_COMMAND_PLACEHOLDER_MESSAGE = (
        "`target.shell` already includes a shell command payload. Add `{command}` where AgentFlow should inject "
        "the prepared agent command."
    )

    def _shell_executable_index(self, shell_parts: list[str]) -> int | None:
        for index, part in enumerate(shell_parts):
            if os.path.basename(part) in self._KNOWN_SHELL_EXECUTABLES:
                return index
        if not shell_parts:
            return None
        return 0

    def _looks_like_env_assignment(self, token: str) -> bool:
        if "=" not in token or token.startswith("="):
            return False
        name, _ = token.split("=", 1)
        if not name:
            return False
        return name.replace("_", "a").isalnum() and not name[0].isdigit()

    def _env_wrapper_shell_index(self, command: list[str]) -> int | None:
        if not command or os.path.basename(command[0]) != "env":
            return None

        position = 1
        ignore_environment = False
        while position < len(command):
            token = command[position]
            if token == "--":
                position += 1
                break
            if token in {"-i", "--ignore-environment"}:
                ignore_environment = True
                position += 1
                continue
            if token == "-u":
                position += 2
                continue
            if token.startswith("--unset=") or (token.startswith("-u") and len(token) > 2):
                position += 1
                continue
            if token.startswith("-"):
                position += 1
                continue
            if self._looks_like_env_assignment(token):
                position += 1
                continue
            break

        if not ignore_environment or position >= len(command):
            return None
        return position

    def _env_wrapper_reserved_names(self, command: list[str], shell_index: int) -> set[str]:
        reserved: set[str] = set()
        position = 1
        while position < shell_index:
            token = command[position]
            if token == "--":
                break
            if token == "-u" and position + 1 < shell_index:
                reserved.add(command[position + 1])
                position += 2
                continue
            if token.startswith("--unset="):
                reserved.add(token.split("=", 1)[1])
                position += 1
                continue
            if token.startswith("-u") and len(token) > 2:
                reserved.add(token[2:])
                position += 1
                continue
            if self._looks_like_env_assignment(token):
                reserved.add(token.split("=", 1)[0])
            position += 1
        return reserved

    def _inline_env_wrapper_assignments(self, command: list[str], env: dict[str, str]) -> list[str]:
        shell_index = self._env_wrapper_shell_index(command)
        if shell_index is None or not env:
            return command

        reserved_names = self._env_wrapper_reserved_names(command, shell_index)
        assignments = [f"{key}={value}" for key, value in env.items() if key not in reserved_names]
        if not assignments:
            return command
        return [*command[:shell_index], *assignments, *command[shell_index:]]

    def _has_flag(self, shell_parts: list[str], short_flag: str, long_flag: str | None = None) -> bool:
        shell_index = self._shell_executable_index(shell_parts)
        if shell_index is None:
            return False
        return any(
            part == long_flag or (part.startswith("-") and not part.startswith("--") and short_flag in part[1:])
            for part in shell_parts[shell_index + 1 :]
        )

    def _command_flag_index(self, shell_parts: list[str]) -> int | None:
        shell_index = self._shell_executable_index(shell_parts)
        if shell_index is None:
            return None
        for index, part in enumerate(shell_parts[shell_index + 1 :], start=shell_index + 1):
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

    def _normalize_shell_command(self, shell_parts: list[str]) -> list[str]:
        normalized = list(shell_parts)
        while normalized and normalized[0] in self._SHELL_BUILTIN_PREFIX_TOKENS:
            normalized.pop(0)
        return normalized

    def _augment_local_env(self, prepared: PreparedExecution, paths: ExecutionPaths) -> dict[str, str]:
        return dict(prepared.env)

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
            shell_parts = self._normalize_shell_command(shlex.split(target.shell.replace("{command}", placeholder)))
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

        shell_parts = self._normalize_shell_command(shlex.split(target.shell))
        shell_parts = self._apply_shell_options(shell_parts, target)
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
        command = self._inline_env_wrapper_assignments(command, plan_env)
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

    async def _wait_for_exit(self, wait_task: asyncio.Task[int], timeout: float) -> bool:
        if wait_task.done():
            return True
        try:
            await asyncio.wait_for(asyncio.shield(wait_task), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return True

    def _signal_process_group(self, process_group_id: int | None, sig: int) -> None:
        if process_group_id is None:
            return
        with suppress(ProcessLookupError):
            os.killpg(process_group_id, sig)

    async def _terminate_with_fallback(self, process, wait_task: asyncio.Task[int], process_group_id: int | None) -> None:
        self._signal_process_group(process_group_id, signal.SIGTERM)
        with suppress(ProcessLookupError):
            process.terminate()
        if await self._wait_for_exit(wait_task, self._TERMINATE_GRACE_SECONDS):
            return
        self._signal_process_group(process_group_id, signal.SIGKILL)
        with suppress(ProcessLookupError):
            process.kill()
        await self._wait_for_exit(wait_task, self._TERMINATE_GRACE_SECONDS)

    async def _emit_stream_line(
        self,
        node: NodeSpec,
        stream_name: str,
        buffer: list[str],
        on_output: StreamCallback,
        text: str,
    ) -> None:
        if stream_name == "stderr" and self._should_suppress_stderr(node, text):
            return
        buffer.append(text)
        await on_output(stream_name, text)

    async def _consume_stream(self, node: NodeSpec, stream, stream_name: str, buffer: list[str], on_output: StreamCallback) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        while True:
            chunk = await stream.read(self._STREAM_READ_SIZE)
            if not chunk:
                break
            pending += decoder.decode(chunk)
            while True:
                newline_index = pending.find("\n")
                if newline_index < 0:
                    break
                text = pending[:newline_index].rstrip("\r")
                pending = pending[newline_index + 1 :]
                await self._emit_stream_line(node, stream_name, buffer, on_output, text)
        pending += decoder.decode(b"", final=True)
        if pending:
            await self._emit_stream_line(node, stream_name, buffer, on_output, pending.rstrip("\r"))

    def _task_exception(self, task: asyncio.Task[object]) -> BaseException | None:
        if not task.done() or task.cancelled():
            return None
        return task.exception()

    async def _await_tasks(self, tasks: tuple[asyncio.Task[object], ...], timeout: float) -> set[asyncio.Task[object]]:
        pending = {task for task in tasks if not task.done()}
        if not pending:
            return set()
        _, pending = await asyncio.wait(pending, timeout=max(timeout, 0.0))
        return pending

    async def execute(
        self,
        node: NodeSpec,
        prepared: PreparedExecution,
        paths: ExecutionPaths,
        on_output: StreamCallback,
        should_cancel,
    ) -> RawExecutionResult:
        self.materialize_runtime_files(paths.host_runtime_dir, prepared.runtime_files)
        ensure_dir(Path(prepared.cwd))
        launch_env = self._augment_local_env(prepared, paths)
        command, target_env = self._command_for_target(node, prepared)
        launch_env.update(target_env)
        env = os.environ.copy()
        env.update(launch_env)
        command = self._inline_env_wrapper_assignments(command, launch_env)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=prepared.cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if prepared.stdin is not None else None,
            start_new_session=True,
        )
        process_group_id: int | None = None
        with suppress(ProcessLookupError):
            process_group_id = os.getpgid(process.pid)
        if prepared.stdin is not None and process.stdin is not None:
            process.stdin.write(prepared.stdin.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        stdout_task = asyncio.create_task(self._consume_stream(node, process.stdout, "stdout", stdout_lines, on_output))
        stderr_task = asyncio.create_task(self._consume_stream(node, process.stderr, "stderr", stderr_lines, on_output))
        wait_task = asyncio.create_task(process.wait())
        managed_tasks: tuple[asyncio.Task[object], ...] = (wait_task, stdout_task, stderr_task)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + node.timeout_seconds
        timed_out = False
        cancelled = False
        stream_failure: BaseException | None = None

        try:
            while True:
                if wait_task.done() and stdout_task.done() and stderr_task.done():
                    break
                stream_failure = self._task_exception(stdout_task) or self._task_exception(stderr_task)
                if stream_failure is not None:
                    await self._terminate_with_fallback(process, wait_task, process_group_id)
                    break
                if should_cancel():
                    cancelled = True
                    await self._terminate_with_fallback(process, wait_task, process_group_id)
                    break
                if loop.time() >= deadline:
                    timed_out = True
                    await self._terminate_with_fallback(process, wait_task, process_group_id)
                    break
                await asyncio.sleep(0.1)
            drain_timeout = self._TERMINATE_GRACE_SECONDS if (timed_out or cancelled or stream_failure is not None) else max(deadline - loop.time(), 0.0)
            pending = await self._await_tasks(managed_tasks, timeout=drain_timeout)
            if pending:
                self._signal_process_group(process_group_id, signal.SIGKILL)
                with suppress(ProcessLookupError):
                    process.kill()
                for task in pending:
                    task.cancel()
        finally:
            await asyncio.gather(*managed_tasks, return_exceptions=True)
            if stream_failure is None:
                stream_failure = self._task_exception(stdout_task) or self._task_exception(stderr_task)
            if timed_out:
                stderr_lines.append(f"Timed out after {node.timeout_seconds}s")
                await on_output("stderr", stderr_lines[-1])
            if cancelled:
                stderr_lines.append("Cancelled by user")
                await on_output("stderr", stderr_lines[-1])
            if stream_failure is not None:
                stderr_lines.append(f"Local runner stream handling failed: {stream_failure}")
                await on_output("stderr", stderr_lines[-1])
            self._signal_process_group(process_group_id, signal.SIGKILL)
            with suppress(ProcessLookupError):
                if process.returncode is None:
                    process.kill()
            if not wait_task.done():
                wait_task.cancel()
                await asyncio.gather(wait_task, return_exceptions=True)

        if cancelled:
            exit_code = 130
        elif timed_out:
            exit_code = 124
        elif stream_failure is not None:
            exit_code = 1
        else:
            exit_code = process.returncode if process.returncode is not None else 0
        return RawExecutionResult(
            exit_code=exit_code,
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines,
            timed_out=timed_out,
            cancelled=cancelled,
        )
