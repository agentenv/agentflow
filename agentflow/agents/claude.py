from __future__ import annotations

import json
import os
from pathlib import Path

from agentflow.agents.base import AgentAdapter
from agentflow.agents.secrets import wrap_secret_files
from agentflow.env import merge_env_layers
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.specs import NodeSpec, RepoInstructionsMode, ToolAccess


_CLAUDE_READ_ONLY_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "LS",
    "NotebookRead",
    "Task",
    "TaskOutput",
    "TodoRead",
    "WebFetch",
    "WebSearch",
]

_CLAUDE_READ_WRITE_TOOLS = _CLAUDE_READ_ONLY_TOOLS + [
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "TodoWrite",
    "Bash",
]


class ClaudeAdapter(AgentAdapter):
    def prepare(self, node: NodeSpec, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        self.validate_node_features(node)
        provider = self.provider_config(node.provider, node.agent)
        executable = node.executable or "claude"
        repo_instructions_ignored = node.repo_instructions_mode == RepoInstructionsMode.IGNORE
        command = [
            executable,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "bypassPermissions",
        ]
        if repo_instructions_ignored:
            command.extend(["--bare", "--add-dir", paths.target_workdir])
        minimax_preset = isinstance(node.provider, str) and node.provider.strip().lower() in {"minimax", "minimax-cn"}
        selected_model = node.model or ("MiniMax-M3.1-Flash" if minimax_preset else None)
        if selected_model:
            command.extend(["--model", selected_model])
        allowed_tools = _CLAUDE_READ_ONLY_TOOLS if node.tools == ToolAccess.READ_ONLY else _CLAUDE_READ_WRITE_TOOLS
        if node.model_settings.web_search == "disabled":
            allowed_tools = [tool for tool in allowed_tools if tool not in {"WebFetch", "WebSearch"}]
        command.extend(["--tools", ",".join(allowed_tools)])
        if node.model_settings.max_turns:
            command.extend(["--max-turns", str(node.model_settings.max_turns)])
        effort = node.model_settings.reasoning_effort
        if minimax_preset and selected_model == "MiniMax-M3.1-Flash":
            if effort not in {None, "low", "medium", "high", "xhigh", "max"}:
                raise ValueError("MiniMax-M3.1-Flash requires effort low, medium, high, xhigh, or max")
            effort = effort or "max"
        if effort:
            command.extend(["--effort", effort])
        runtime_files: dict[str, str] = {}
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
                        inner["env"] = dict(mcp.env)
                    if mcp.secret_env:
                        inner.setdefault("env", {}).update({key: "${" + key + "}" for key in mcp.secret_env})
                else:
                    if mcp.url:
                        inner["url"] = mcp.url
                    if mcp.headers:
                        inner["headers"] = dict(mcp.headers)
                    inner.setdefault("headers", {}).update({key: "${" + value + "}" for key, value in mcp.env_http_headers.items()})
                    if mcp.bearer_token_env_var:
                        inner["headers"]["Authorization"] = "Bearer ${" + mcp.bearer_token_env_var + "}"
                    inner["type"] = "http"
                mcp_payload["mcpServers"][mcp.name] = inner
            relative_path = self.relative_runtime_file("claude-mcp.json")
            runtime_files[relative_path] = json.dumps(mcp_payload, ensure_ascii=False, indent=2)
            command.extend(["--mcp-config", str(Path(paths.target_runtime_dir) / relative_path)])
        env = merge_env_layers(getattr(provider, "env", None), node.env)
        if node.model_settings.max_output_tokens:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(node.model_settings.max_output_tokens)
        is_docker = node.target.kind == "docker"
        if is_docker and node.target.inherit_credentials:
            raise ValueError("Docker agent nodes must use explicit secret files, not host credentials")
        if is_docker:
            env["CLAUDE_CONFIG_DIR"] = str(Path(paths.target_runtime_dir) / "claude-home")
            env["HOME"] = str(Path(paths.target_runtime_dir) / "claude-home")
        if provider:
            if provider.base_url:
                env.setdefault("ANTHROPIC_BASE_URL", provider.base_url)
            if provider.headers:
                env.setdefault("ANTHROPIC_CUSTOM_HEADERS", json.dumps(provider.headers, ensure_ascii=False))
            if provider.api_key_env:
                if provider.api_key_env in env:
                    api_key = env[provider.api_key_env]
                elif not is_docker:
                    api_key = os.getenv(provider.api_key_env)
                else:
                    api_key = None
                if api_key is not None:
                    env.setdefault("ANTHROPIC_API_KEY", api_key)
        command.extend(node.extra_args)
        cwd = paths.target_workdir
        if repo_instructions_ignored:
            cwd = str(Path(paths.target_runtime_dir))
        prepared = PreparedExecution(
            command=command,
            env=env,
            cwd=cwd,
            trace_kind="claude",
            runtime_files=runtime_files,
        )
        aliases = {"ANTHROPIC_API_KEY": provider.api_key_env} if is_docker and provider and provider.api_key_env and provider.api_key_env != "ANTHROPIC_API_KEY" else None
        return wrap_secret_files(node, prepared, paths, aliases=aliases)
