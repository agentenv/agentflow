"""Generate installation scripts for agent CLIs on cloud instances."""
from __future__ import annotations

import json
import shlex


def agent_install_script(agents: list[str]) -> str:
    """Return a bash script that installs the requested agent CLIs.

    Supported agents: codex, claude, kimi, gemini.
    """
    lines = ["#!/bin/bash", "set -euo pipefail", "export DEBIAN_FRONTEND=noninteractive", ""]

    # Install common dependencies
    lines.append("# Install Node.js (needed for codex)")
    lines.append("if ! command -v node &>/dev/null; then")
    lines.append("  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -")
    lines.append("  apt-get install -y nodejs")
    lines.append("fi")
    lines.append("")

    for agent in agents:
        if agent == "codex":
            lines.append("# Install Codex CLI")
            lines.append("if ! command -v codex &>/dev/null; then")
            lines.append("  npm install -g @openai/codex")
            lines.append("fi")
        elif agent == "claude":
            lines.append("# Install Claude Code CLI")
            lines.append("if ! command -v claude &>/dev/null; then")
            lines.append("  npm install -g @anthropic-ai/claude-code")
            lines.append("fi")
        elif agent == "kimi":
            lines.append("# Install Kimi CLI")
            lines.append("if ! command -v kimi &>/dev/null; then")
            lines.append("  pip3 install kimi-cli || pip install kimi-cli")
            lines.append("fi")
        elif agent == "gemini":
            lines.append("# Install Gemini CLI")
            lines.append("if ! command -v gemini &>/dev/null; then")
            lines.append("  npm install -g @google/gemini-cli")
            lines.append("fi")
        lines.append("")

    lines.append("echo 'Agent installation complete'")
    return "\n".join(lines)


def agent_dockerfile(agents: list[str], base_image: str = "ubuntu:24.04") -> str:
    """Return a Dockerfile that installs the requested agent CLIs."""
    lines = [f"FROM {base_image}", ""]
    lines.append("RUN apt-get update && apt-get install -y curl python3 python3-pip git && rm -rf /var/lib/apt/lists/*")
    lines.append("")
    lines.append("# Install Node.js")
    lines.append("RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs")
    lines.append("")

    for agent in agents:
        if agent == "codex":
            lines.append("RUN npm install -g @openai/codex")
        elif agent == "claude":
            lines.append("RUN npm install -g @anthropic-ai/claude-code")
        elif agent == "kimi":
            lines.append("RUN pip3 install kimi-cli")
        elif agent == "gemini":
            lines.append("RUN npm install -g @google/gemini-cli")

    lines.append("")
    lines.append("WORKDIR /workspace")
    lines.append('ENTRYPOINT ["/bin/bash", "-c"]')
    return "\n".join(lines)


def agent_auth_setup(agent: str, env: dict[str, str]) -> str:
    """Return a bash snippet that writes auth config for an agent CLI.

    Reads API keys and base URLs from *env* and writes the config files
    that each CLI expects. Call this before the agent command so the
    CLI finds credentials automatically.
    """
    parts: list[str] = []

    if agent == "codex":
        api_key = env.get("OPENAI_API_KEY", "")
        base_url = env.get("OPENAI_BASE_URL", "")
        if api_key:
            auth = json.dumps({"OPENAI_API_KEY": api_key})
            parts.append(f"mkdir -p ~/.codex")
            parts.append(f"echo {shlex.quote(auth)} > ~/.codex/auth.json")
        if base_url:
            parts.append(f"mkdir -p ~/.codex")
            config_lines = [
                '[model_providers.OpenAI]',
                'name = "OpenAI"',
                f'base_url = "{base_url}"',
                'wire_api = "responses"',
                'requires_openai_auth = true',
            ]
            config_str = "\\n".join(config_lines)
            parts.append(f'printf {shlex.quote(config_str + chr(10))} > ~/.codex/config.toml')
    elif agent == "claude":
        api_key = env.get("ANTHROPIC_API_KEY", "")
        if api_key:
            # Write OAuth credentials file so claude CLI picks it up
            creds = json.dumps({"claudeAiOauth": {"accessToken": api_key}})
            parts.append(f"mkdir -p ~/.claude")
            parts.append(f"echo {shlex.quote(creds)} > ~/.claude/.credentials.json")
            # Also export env var as fallback
            parts.append(f"export ANTHROPIC_API_KEY={shlex.quote(api_key)}")
        base_url = env.get("ANTHROPIC_BASE_URL", "")
        if base_url:
            parts.append(f"export ANTHROPIC_BASE_URL={shlex.quote(base_url)}")
    elif agent == "kimi":
        api_key = env.get("KIMI_API_KEY", "") or env.get("MOONSHOT_API_KEY", "")
        if api_key:
            parts.append(f"export KIMI_API_KEY={shlex.quote(api_key)}")
            parts.append(f"export MOONSHOT_API_KEY={shlex.quote(api_key)}")
    elif agent == "gemini":
        api_key = env.get("GEMINI_API_KEY", "") or env.get("GOOGLE_API_KEY", "")
        if api_key:
            parts.append(f"export GEMINI_API_KEY={shlex.quote(api_key)}")

    return " && ".join(parts) if parts else ""
