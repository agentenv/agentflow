from __future__ import annotations

import os
from pathlib import Path

from agentflow.agents.base import AgentAdapter
from agentflow.env import merge_env_layers
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.specs import NodeSpec, RepoInstructionsMode, ToolAccess


class GeminiAdapter(AgentAdapter):
    def prepare(self, node: NodeSpec, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        provider = self.provider_config(node.provider, node.agent)
        executable = node.executable or "gemini"

        # Use -p for non-interactive (headless) mode; positional prompt
        # launches interactive mode which hangs in automation.
        command = [
            executable,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
        ]

        # Permission flags: map tools access to Gemini's approval model.
        # --approval-mode plan = read-only (no writes allowed).
        # --yolo = auto-approve all tool calls (required for non-interactive write).
        if node.tools == ToolAccess.READ_ONLY:
            command.extend(["--sandbox", "--approval-mode", "plan"])
        else:
            command.extend(["--yolo"])

        if node.model:
            command.extend(["--model", node.model])

        runtime_files: dict[str, str] = {}

        repo_instructions_ignored = node.repo_instructions_mode == RepoInstructionsMode.IGNORE
        if repo_instructions_ignored:
            # Gemini reads GEMINI.md for repo instructions; running from runtime dir avoids it
            pass

        command.extend(node.extra_args)

        env = merge_env_layers(getattr(provider, "env", None), node.env)
        if provider:
            if provider.api_key_env:
                if provider.api_key_env in env:
                    api_key = env[provider.api_key_env]
                else:
                    api_key = os.getenv(provider.api_key_env)
                if api_key is not None:
                    env.setdefault("GEMINI_API_KEY", api_key)

        cwd = paths.target_workdir
        if repo_instructions_ignored:
            cwd = str(Path(paths.target_runtime_dir))

        return PreparedExecution(
            command=command,
            env=env,
            cwd=cwd,
            trace_kind="gemini",
            runtime_files=runtime_files,
        )
