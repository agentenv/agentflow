from __future__ import annotations

from pathlib import Path

from agentflow.agents.base import AgentAdapter
from agentflow.env import merge_env_layers
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.specs import NodeSpec, ProviderConfig, RepoInstructionsMode, ToolAccess


class CodexAdapter(AgentAdapter):
    _SUPPORTED_SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}

    def _format_toml_value(self, value: object) -> str:
        import json

        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            return "[" + ", ".join(self._format_toml_value(item) for item in value) + "]"
        if isinstance(value, dict):
            items = ", ".join(f"{key} = {self._format_toml_value(inner)}" for key, inner in value.items())
            return "{" + items + "}"
        return json.dumps(str(value), ensure_ascii=False)

    def _render_config(self, node: NodeSpec, provider: ProviderConfig | None, sandbox_mode: str) -> str:
        lines: list[str] = []
        if node.model:
            lines.append(f"model = {self._format_toml_value(node.model)}")
        lines.append(f"approval_policy = {self._format_toml_value('never')}")
        lines.append(f"sandbox_mode = {self._format_toml_value(sandbox_mode)}")
        if provider and (provider.base_url or provider.api_key_env or provider.wire_api):
            lines.append("")
            lines.append(f"[model_providers.{provider.name}]")
            lines.append(f"name = {self._format_toml_value(provider.name)}")
            if provider.base_url:
                lines.append(f"base_url = {self._format_toml_value(provider.base_url)}")
            if provider.api_key_env:
                lines.append(f"env_key = {self._format_toml_value(provider.api_key_env)}")
            if provider.wire_api:
                lines.append(f"wire_api = {self._format_toml_value(provider.wire_api)}")
        if provider:
            lines.append("")
            lines.append("[profiles.agentflow]")
            if node.model:
                lines.append(f"model = {self._format_toml_value(node.model)}")
            lines.append(f"model_provider = {self._format_toml_value(provider.name)}")
        if node.mcps:
            for mcp in node.mcps:
                lines.append("")
                lines.append(f"[mcp_servers.{mcp.name}]")
                if mcp.transport == "stdio":
                    if mcp.command:
                        lines.append(f"command = {self._format_toml_value(mcp.command)}")
                    if mcp.args:
                        lines.append(f"args = {self._format_toml_value(mcp.args)}")
                    if mcp.env:
                        lines.append(f"env = {self._format_toml_value(mcp.env)}")
                else:
                    if mcp.url:
                        lines.append(f"url = {self._format_toml_value(mcp.url)}")
                    if mcp.headers:
                        lines.append(f"http_headers = {self._format_toml_value(mcp.headers)}")
        return "\n".join(lines) + "\n"

    def _resolve_sandbox_mode(self, node: NodeSpec, env: dict[str, str]) -> str:
        override = (env.pop("AGENTFLOW_CODEX_SANDBOX_MODE", "") or "").strip()
        if not override:
            return "read-only" if node.tools == ToolAccess.READ_ONLY else "workspace-write"
        if override not in self._SUPPORTED_SANDBOX_MODES:
            raise ValueError(
                "AGENTFLOW_CODEX_SANDBOX_MODE must be one of: "
                + ", ".join(sorted(self._SUPPORTED_SANDBOX_MODES))
            )
        return override

    _WRAPPER_FILENAME = "agentflow_wrapper.md"
    _WRAPPER_SEPARATOR = "\n\n---\n\n"

    def _maybe_prepend_wrapper(self, node: NodeSpec, prompt: str) -> str:
        """Prepend an agentflow-side wrapper to the user prompt if one exists.

        For tuned codex builds the executable lives at
        ``<version>/repo/codex-rs/target/debug/codex``; we look for
        ``<version>/repo/codex-rs/agentflow_wrapper.md`` next to it. This is
        the most reliable evolution surface because the wrapper text becomes
        part of the user message — gateways that override server-side system
        prompts cannot strip it.
        """
        executable = node.executable
        if not executable:
            return prompt
        exec_path = Path(executable).expanduser()
        if not exec_path.is_absolute():
            return prompt
        # codex_tuned binary path: .../codex-rs/target/debug/codex
        # → walk up three parents to reach codex-rs/
        if len(exec_path.parents) < 3:
            return prompt
        codex_rs_root = exec_path.parents[2]
        wrapper_path = codex_rs_root / self._WRAPPER_FILENAME
        if not wrapper_path.is_file():
            return prompt
        try:
            wrapper_text = wrapper_path.read_text(encoding="utf-8").strip()
        except OSError:
            return prompt
        if not wrapper_text:
            return prompt
        return wrapper_text + self._WRAPPER_SEPARATOR + prompt

    def prepare(self, node: NodeSpec, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        provider = self.provider_config(node.provider, node.agent)
        executable = node.executable or "codex"
        env = merge_env_layers(getattr(provider, "env", None), node.env)
        sandbox = self._resolve_sandbox_mode(node, env)
        repo_instructions_ignored = node.repo_instructions_mode == RepoInstructionsMode.IGNORE
        command = [
            executable,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "-c",
            'approval_policy="never"',
            "-c",
            "suppress_unstable_features_warning=true",
            "--sandbox",
            sandbox,
        ]
        if node.model and not provider:
            command.extend(["--model", node.model])
        if provider:
            command.extend(["--profile", "agentflow"])
        if repo_instructions_ignored:
            command.extend(["--disable", "plugins"])
            command.extend(["--add-dir", paths.target_workdir])
        command.extend(node.extra_args)
        prompt = self._maybe_prepend_wrapper(node, prompt)
        command.append(prompt)

        runtime_files: dict[str, str] = {}
        runtime_symlinks: dict[str, str] = {}
        is_docker_target = getattr(node.target, "kind", None) == "docker"
        inherit_host_credentials = not is_docker_target or bool(
            getattr(node.target, "inherit_credentials", False)
        )
        needs_scoped_home = bool(
            provider
            or node.mcps
            or repo_instructions_ignored
            or (is_docker_target and inherit_host_credentials)
        )
        if needs_scoped_home:
            codex_home = str(Path(paths.target_runtime_dir) / "codex_home")
            host_config = Path.home() / ".codex" / "config.toml"
            inherit_host_config = (
                inherit_host_credentials
                and provider is None
                and not node.mcps
                and host_config.is_file()
            )
            if inherit_host_config:
                runtime_symlinks[self.relative_runtime_file("codex_home", "config.toml")] = str(host_config)
            else:
                runtime_files[self.relative_runtime_file("codex_home", "config.toml")] = self._render_config(
                    node,
                    provider,
                    sandbox,
                )
            host_auth = Path.home() / ".codex" / "auth.json"
            if inherit_host_credentials and host_auth.is_file():
                runtime_symlinks[self.relative_runtime_file("codex_home", "auth.json")] = str(host_auth)
            env["CODEX_HOME"] = codex_home
            env["HOME"] = codex_home
        cwd = paths.target_workdir
        if repo_instructions_ignored:
            cwd = str(Path(paths.target_runtime_dir))
        return PreparedExecution(
            command=command,
            env=env,
            cwd=cwd,
            trace_kind="codex",
            runtime_files=runtime_files,
            runtime_symlinks=runtime_symlinks,
        )
