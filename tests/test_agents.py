from __future__ import annotations

import json
from pathlib import Path

from agentflow.agents.claude import ClaudeAdapter
from agentflow.agents.codex import CodexAdapter
from agentflow.agents.kimi import KimiAdapter
from agentflow.prepared import ExecutionPaths
from agentflow.specs import NodeSpec


def _paths(tmp_path: Path) -> ExecutionPaths:
    return ExecutionPaths(
        host_workdir=tmp_path,
        host_runtime_dir=tmp_path / ".runtime",
        target_workdir=str(tmp_path),
        target_runtime_dir=str(tmp_path / ".runtime"),
        app_root=tmp_path,
    )


def test_claude_adapter_uses_provider_api_key_env_value(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_CLAUDE_API_KEY", "test-secret")
    node = NodeSpec.model_validate(
        {
            "id": "review",
            "agent": "claude",
            "prompt": "Review",
            "provider": {
                "name": "kimi-proxy",
                "base_url": "https://example.test/anthropic",
                "api_key_env": "TEST_CLAUDE_API_KEY",
                "headers": {"x-provider": "kimi"},
            },
        }
    )

    prepared = ClaudeAdapter().prepare(node, "Review", _paths(tmp_path))

    assert prepared.env["ANTHROPIC_BASE_URL"] == "https://example.test/anthropic"
    assert prepared.env["ANTHROPIC_API_KEY"] == "test-secret"
    assert json.loads(prepared.env["ANTHROPIC_CUSTOM_HEADERS"]) == {"x-provider": "kimi"}
    assert "ANTHROPIC_API_KEY_ENV" not in prepared.env


def test_codex_adapter_uses_current_exec_flags(tmp_path):
    node = NodeSpec.model_validate(
        {
            "id": "plan",
            "agent": "codex",
            "prompt": "Plan",
        }
    )

    prepared = CodexAdapter().prepare(node, "Plan", _paths(tmp_path))

    assert prepared.command[:4] == ["codex", "exec", "--json", "--skip-git-repo-check"]
    assert "--ask-for-approval" not in prepared.command
    assert prepared.command[4:10] == [
        "-c",
        'approval_policy="never"',
        "-c",
        "suppress_unstable_features_warning=true",
        "--sandbox",
        "read-only",
    ]


def test_codex_adapter_suppresses_unstable_feature_warning(tmp_path):
    node = NodeSpec.model_validate(
        {
            "id": "plan",
            "agent": "codex",
            "prompt": "Plan",
        }
    )

    prepared = CodexAdapter().prepare(node, "Plan", _paths(tmp_path))

    assert prepared.command.count("-c") == 2
    assert 'suppress_unstable_features_warning=true' in prepared.command


def test_claude_adapter_uses_tools_flag_for_read_only_access(tmp_path):
    node = NodeSpec.model_validate(
        {
            "id": "review",
            "agent": "claude",
            "prompt": "Review",
        }
    )

    prepared = ClaudeAdapter().prepare(node, "Review", _paths(tmp_path))

    assert "--allowedTools" not in prepared.command
    index = prepared.command.index("--tools")
    assert prepared.command[index + 1] == "Read,Glob,Grep,LS,NotebookRead,Task,TaskOutput,TodoRead,WebFetch,WebSearch"


def test_claude_adapter_uses_tools_flag_for_read_write_access(tmp_path):
    node = NodeSpec.model_validate(
        {
            "id": "implement",
            "agent": "claude",
            "prompt": "Implement",
            "tools": "read_write",
        }
    )

    prepared = ClaudeAdapter().prepare(node, "Implement", _paths(tmp_path))

    index = prepared.command.index("--tools")
    assert "Bash" in prepared.command[index + 1].split(",")
    assert "Write" in prepared.command[index + 1].split(",")


def test_claude_adapter_supports_kimi_provider_alias(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-kimi-secret")
    node = NodeSpec.model_validate(
        {
            "id": "review",
            "agent": "claude",
            "prompt": "Review",
            "provider": "kimi",
        }
    )

    prepared = ClaudeAdapter().prepare(node, "Review", _paths(tmp_path))

    assert prepared.env["ANTHROPIC_BASE_URL"] == "https://api.kimi.com/coding/"
    assert prepared.env["ANTHROPIC_API_KEY"] == "test-kimi-secret"


def test_kimi_adapter_uses_kimi_cli_directly(tmp_path):
    node = NodeSpec.model_validate(
        {
            "id": "review",
            "agent": "kimi",
            "prompt": "Review",
        }
    )

    prepared = KimiAdapter().prepare(node, "Review", _paths(tmp_path))

    assert prepared.command[0] == "kimi"
    assert "--print" in prepared.command
    assert "--output-format" in prepared.command
    assert "stream-json" in prepared.command
    assert "--yolo" in prepared.command
    assert "-p" in prepared.command
    assert "Review" in prepared.command


def test_kimi_adapter_passes_model_flag(tmp_path):
    node = NodeSpec.model_validate(
        {
            "id": "review",
            "agent": "kimi",
            "prompt": "Review",
            "model": "kimi-k2-turbo-preview",
        }
    )

    prepared = KimiAdapter().prepare(node, "Review", _paths(tmp_path))

    assert "--model" in prepared.command
    model_index = prepared.command.index("--model")
    assert prepared.command[model_index + 1] == "kimi-k2-turbo-preview"


def test_kimi_adapter_respects_custom_executable(tmp_path):
    node = NodeSpec.model_validate(
        {
            "id": "review",
            "agent": "kimi",
            "prompt": "Review",
            "executable": "/usr/local/bin/kimi",
        }
    )

    prepared = KimiAdapter().prepare(node, "Review", _paths(tmp_path))

    assert prepared.command[0] == "/usr/local/bin/kimi"


def test_claude_adapter_prefers_node_env_over_provider_env(tmp_path):
    node = NodeSpec.model_validate(
        {
            "id": "review",
            "agent": "claude",
            "prompt": "Review",
            "env": {"SHARED_FLAG": "node", "ANTHROPIC_API_KEY": "node-secret"},
            "provider": {
                "name": "kimi-proxy",
                "base_url": "https://example.test/anthropic",
                "api_key_env": "ANTHROPIC_API_KEY",
                "env": {"SHARED_FLAG": "provider", "ANTHROPIC_API_KEY": "provider-secret"},
            },
        }
    )

    prepared = ClaudeAdapter().prepare(node, "Review", _paths(tmp_path))

    assert prepared.env["SHARED_FLAG"] == "node"
    assert prepared.env["ANTHROPIC_API_KEY"] == "node-secret"


def test_claude_adapter_respects_node_env_clear_for_custom_provider_key(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_CLAUDE_API_KEY", "ambient-secret")
    node = NodeSpec.model_validate(
        {
            "id": "review",
            "agent": "claude",
            "prompt": "Review",
            "env": {"TEST_CLAUDE_API_KEY": ""},
            "provider": {
                "name": "kimi-proxy",
                "base_url": "https://example.test/anthropic",
                "api_key_env": "TEST_CLAUDE_API_KEY",
            },
        }
    )

    prepared = ClaudeAdapter().prepare(node, "Review", _paths(tmp_path))

    assert prepared.env["TEST_CLAUDE_API_KEY"] == ""
    assert prepared.env["ANTHROPIC_API_KEY"] == ""


def test_claude_adapter_respects_provider_env_clear_for_custom_provider_key(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_CLAUDE_API_KEY", "ambient-secret")
    node = NodeSpec.model_validate(
        {
            "id": "review",
            "agent": "claude",
            "prompt": "Review",
            "provider": {
                "name": "kimi-proxy",
                "base_url": "https://example.test/anthropic",
                "api_key_env": "TEST_CLAUDE_API_KEY",
                "env": {"TEST_CLAUDE_API_KEY": ""},
            },
        }
    )

    prepared = ClaudeAdapter().prepare(node, "Review", _paths(tmp_path))

    assert prepared.env["TEST_CLAUDE_API_KEY"] == ""
    assert prepared.env["ANTHROPIC_API_KEY"] == ""


def test_codex_adapter_prefers_node_env_over_provider_env(tmp_path):
    node = NodeSpec.model_validate(
        {
            "id": "plan",
            "agent": "codex",
            "prompt": "Plan",
            "env": {"SHARED_FLAG": "node", "OPENAI_API_KEY": "node-secret"},
            "provider": {
                "name": "openai-proxy",
                "base_url": "https://example.test/openai",
                "api_key_env": "OPENAI_API_KEY",
                "wire_api": "responses",
                "env": {"SHARED_FLAG": "provider", "OPENAI_API_KEY": "provider-secret"},
            },
        }
    )

    prepared = CodexAdapter().prepare(node, "Plan", _paths(tmp_path))

    assert prepared.env["SHARED_FLAG"] == "node"
    assert prepared.env["OPENAI_API_KEY"] == "node-secret"


def test_codex_adapter_preserves_empty_openai_base_url_override(tmp_path):
    node = NodeSpec.model_validate(
        {
            "id": "plan",
            "agent": "codex",
            "prompt": "Plan",
            "env": {"OPENAI_BASE_URL": ""},
        }
    )

    prepared = CodexAdapter().prepare(node, "Plan", _paths(tmp_path))

    assert "OPENAI_BASE_URL" in prepared.env
    assert prepared.env["OPENAI_BASE_URL"] == ""


def test_kimi_adapter_prefers_node_env_over_provider_env(tmp_path):
    node = NodeSpec.model_validate(
        {
            "id": "review",
            "agent": "kimi",
            "prompt": "Review",
            "env": {"SHARED_FLAG": "node", "KIMI_API_KEY": "node-secret"},
            "provider": {
                "name": "moonshot-proxy",
                "base_url": "https://example.test/moonshot",
                "api_key_env": "KIMI_API_KEY",
                "env": {"SHARED_FLAG": "provider", "KIMI_API_KEY": "provider-secret"},
            },
        }
    )

    prepared = KimiAdapter().prepare(node, "Review", _paths(tmp_path))

    assert prepared.env["SHARED_FLAG"] == "node"
    assert prepared.env["KIMI_API_KEY"] == "node-secret"
