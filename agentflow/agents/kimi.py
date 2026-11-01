from __future__ import annotations

import json
import os
from pathlib import Path

from agentflow.agents.base import AgentAdapter
from agentflow.env import merge_env_layers
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.specs import NodeSpec, RepoInstructionsMode


class KimiAdapter(AgentAdapter):
    def prepare(self, node: NodeSpec, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        self.validate_node_features(node)
        provider = self.provider_config(node.provider, node.agent)
        executable = node.executable or "kimi"
        repo_instructions_ignored = node.repo_instructions_mode == RepoInstructionsMode.IGNORE
        command = [
            executable,
            "--print",
            "--output-format",
            "stream-json",
            "--yolo",
            "-p",
            prompt,
        ]
        if repo_instructions_ignored:
            empty_skills_dir = Path(paths.target_runtime_dir) / "empty-skills"
            command.extend(["--add-dir", paths.target_workdir, "--skills-dir", str(empty_skills_dir)])
        if node.model:
            command.extend(["--model", node.model])
        runtime_files: dict[str, str] = {}
        if repo_instructions_ignored:
            runtime_files[self.relative_runtime_file("empty-skills", ".gitkeep")] = ""
        if node.mcps:
            mcp_payload: dict[str, object] = {"mcpServers": {}}
            for mcp in node.mcps:
                inner: dict[str, object] = {}
                if mcp.transport == "stdio":
                    if mcp.command:
                        inner["command"] = mcp.command
                    if mcp.args:
                        inner["args"] = mcp.args
                    if mcp.env:
                        inner["env"] = mcp.env
                else:
                    if mcp.url:
                        inner["url"] = mcp.url
                    if mcp.headers:
                        inner["headers"] = mcp.headers
                    inner["transport"] = "streamable_http"
                mcp_payload["mcpServers"][mcp.name] = inner
            relative_path = self.relative_runtime_file("kimi-mcp.json")
            runtime_files[relative_path] = json.dumps(mcp_payload, ensure_ascii=False, indent=2)
            command.extend(["--mcp-config-file", str(Path(paths.target_runtime_dir) / relative_path)])
        command.extend(node.extra_args)
        env = merge_env_layers(getattr(provider, "env", None), node.env)
        if provider:
            if provider.api_key_env:
                if provider.api_key_env in env:
                    api_key = env[provider.api_key_env]
                else:
                    api_key = os.getenv(provider.api_key_env)
                if api_key is not None:
                    env.setdefault("KIMI_API_KEY", api_key)
        cwd = paths.target_workdir
        if repo_instructions_ignored:
            cwd = str(Path(paths.target_runtime_dir))
        return PreparedExecution(
            command=command,
            env=env,
            cwd=cwd,
            trace_kind="kimi",
            runtime_files=runtime_files,
        )
