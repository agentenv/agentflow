from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from agentflow.specs import PipelineSpec, RunEvent, RunRecord
from agentflow.store import RunStore


def test_pipeline_validation_rejects_cycles():
    with pytest.raises(ValueError, match="cycle detected"):
        PipelineSpec.model_validate(
            {
                "name": "cycle",
                "working_dir": ".",
                "nodes": [
                    {"id": "a", "agent": "codex", "prompt": "a", "depends_on": ["b"]},
                    {"id": "b", "agent": "codex", "prompt": "b", "depends_on": ["a"]},
                ],
            }
        )


def test_pipeline_validation_rejects_codex_kimi_provider_alias():
    with pytest.raises(ValueError, match="provider 'kimi' is not supported for codex nodes"):
        PipelineSpec.model_validate(
            {
                "name": "invalid-provider",
                "working_dir": ".",
                "nodes": [
                    {"id": "plan", "agent": "codex", "prompt": "plan", "provider": "kimi"},
                ],
            }
        )


@pytest.mark.parametrize(
    ("target_patch", "expected_field"),
    [
        ({"shell_login": True}, "shell_login"),
        ({"shell_interactive": True}, "shell_interactive"),
        ({"shell_init": "kimi"}, "shell_init"),
        ({"shell_init": ["kimi"]}, "shell_init"),
    ],
)
def test_pipeline_validation_rejects_local_shell_bootstrap_without_shell(target_patch, expected_field):
    with pytest.raises(ValueError, match=rf"{expected_field}.*target\.shell"):
        PipelineSpec.model_validate(
            {
                "name": "invalid-local-shell-target",
                "working_dir": ".",
                "nodes": [
                    {
                        "id": "plan",
                        "agent": "claude",
                        "prompt": "plan",
                        "target": {"kind": "local", **target_patch},
                    },
                ],
            }
        )


@pytest.mark.parametrize(
    ("shell", "expected_message"),
    [
        ("bash --command 'kimi && {command}'", r"unsupported bash long option.*--command.*use `-c`"),
        ("bash --command='kimi && {command}'", r"unsupported bash long option.*--command.*use `-c`"),
        (
            "env BASH_ENV=/tmp/kimi.env bash --interactive -c 'kimi && {command}'",
            r"unsupported bash long option.*--interactive.*target\.shell_interactive",
        ),
        (
            "bash --rcfile=\"$HOME/.bashrc\" -ic 'kimi && {command}'",
            r"unsupported bash long option.*--rcfile=.*pass `--rcfile` and its value as separate arguments",
        ),
        (
            "bash --login=1 -c 'kimi && {command}'",
            r"unsupported bash long option.*--login=.*use `--login` without `=`",
        ),
    ],
)
def test_pipeline_validation_rejects_unsupported_bash_long_options(shell, expected_message):
    with pytest.raises(ValueError, match=expected_message):
        PipelineSpec.model_validate(
            {
                "name": "invalid-bash-long-options",
                "working_dir": ".",
                "nodes": [
                    {
                        "id": "plan",
                        "agent": "claude",
                        "prompt": "plan",
                        "target": {"kind": "local", "shell": shell, "shell_init": "kimi"},
                    },
                ],
            }
        )


def test_pipeline_validation_accepts_supported_bash_long_options_with_separate_value():
    pipeline = PipelineSpec.model_validate(
        {
            "name": "valid-bash-long-options",
            "working_dir": ".",
            "nodes": [
                {
                    "id": "plan",
                    "agent": "claude",
                    "prompt": "plan",
                    "target": {
                        "kind": "local",
                        "shell": "bash --rcfile $HOME/.bashrc -ic 'kimi && {command}'",
                        "shell_init": "kimi",
                    },
                },
            ],
        }
    )

    assert pipeline.nodes[0].target.shell == "bash --rcfile $HOME/.bashrc -ic 'kimi && {command}'"


@pytest.mark.parametrize(
    "shell",
    [
        "bash -lc 'echo pre'",
        "env BASH_ENV=/tmp/shell.env bash -lc 'echo pre'",
    ],
)
def test_pipeline_validation_rejects_shell_command_payload_without_command_placeholder(shell):
    with pytest.raises(ValueError, match=r"shell command payload.*\{command\}"):
        PipelineSpec.model_validate(
            {
                "name": "invalid-shell-command-payload",
                "working_dir": ".",
                "nodes": [
                    {
                        "id": "plan",
                        "agent": "claude",
                        "prompt": "plan",
                        "target": {"kind": "local", "shell": shell},
                    },
                ],
            }
        )


def test_pipeline_validation_applies_local_target_defaults_to_local_nodes():
    pipeline = PipelineSpec.model_validate(
        {
            "name": "local-target-defaults",
            "working_dir": ".",
            "local_target_defaults": {
                "shell": "bash",
                "shell_login": True,
                "shell_interactive": True,
                "shell_init": ["command -v kimi >/dev/null 2>&1", "kimi"],
            },
            "nodes": [
                {"id": "plan", "agent": "codex", "prompt": "plan"},
                {
                    "id": "review",
                    "agent": "claude",
                    "prompt": "review",
                    "target": {"cwd": "review-work", "shell_init": "custom-kimi"},
                },
                {
                    "id": "remote",
                    "agent": "kimi",
                    "prompt": "remote",
                    "target": {"kind": "container", "image": "python:3.12"},
                },
            ],
        }
    )

    assert pipeline.local_target_defaults is not None
    assert pipeline.nodes[0].target.shell == "bash"
    assert pipeline.nodes[0].target.shell_init == ["command -v kimi >/dev/null 2>&1", "kimi"]
    assert pipeline.nodes[1].target.shell == "bash"
    assert pipeline.nodes[1].target.cwd == "review-work"
    assert pipeline.nodes[1].target.shell_init == "custom-kimi"
    assert pipeline.nodes[2].target.kind == "container"


def test_pipeline_validation_accepts_shell_init_command_lists():
    pipeline = PipelineSpec.model_validate(
        {
            "name": "valid-shell-init-list",
            "working_dir": ".",
            "nodes": [
                {
                    "id": "plan",
                    "agent": "claude",
                    "prompt": "plan",
                    "target": {
                        "kind": "local",
                        "shell": "bash",
                        "shell_init": [" command -v kimi >/dev/null 2>&1 ", " kimi "],
                    },
                },
            ],
        }
    )

    assert pipeline.nodes[0].target.shell_init == ["command -v kimi >/dev/null 2>&1", "kimi"]


def test_pipeline_validation_rejects_empty_shell_init_list_entries():
    with pytest.raises(ValidationError, match="non-empty strings"):
        PipelineSpec.model_validate(
            {
                "name": "invalid-shell-init-list",
                "working_dir": ".",
                "nodes": [
                    {
                        "id": "plan",
                        "agent": "claude",
                        "prompt": "plan",
                        "target": {
                            "kind": "local",
                            "shell": "bash",
                            "shell_init": ["kimi", "   "],
                        },
                    },
                ],
            }
        )


@pytest.mark.parametrize(
    ("mcp_patch", "expected_message"),
    [
        ({"transport": "stdio"}, "stdio MCP servers require `command`"),
        (
            {"transport": "stdio", "command": "npx", "url": "https://example.com/mcp"},
            "stdio MCP servers do not support `url`",
        ),
        ({"transport": "streamable_http"}, "streamable_http MCP servers require `url`"),
        (
            {"transport": "streamable_http", "url": "https://example.com/mcp", "command": "npx"},
            "streamable_http MCP servers do not support `command`",
        ),
    ],
)
def test_pipeline_validation_rejects_invalid_mcp_server_shape(mcp_patch, expected_message):
    with pytest.raises(ValueError, match=expected_message):
        PipelineSpec.model_validate(
            {
                "name": "invalid-mcp-shape",
                "working_dir": ".",
                "nodes": [
                    {
                        "id": "plan",
                        "agent": "claude",
                        "prompt": "plan",
                        "mcps": [{"name": "github", **mcp_patch}],
                    },
                ],
            }
        )


def test_pipeline_validation_rejects_duplicate_mcp_server_names():
    with pytest.raises(ValueError, match="duplicate MCP server names"):
        PipelineSpec.model_validate(
            {
                "name": "duplicate-mcps",
                "working_dir": ".",
                "nodes": [
                    {
                        "id": "plan",
                        "agent": "claude",
                        "prompt": "plan",
                        "mcps": [
                            {"name": "github", "command": "npx"},
                            {"name": "github", "command": "node"},
                        ],
                    },
                ],
            }
        )


@pytest.mark.parametrize(
    ("pipeline_patch", "node_patch", "expected_loc", "expected_type"),
    [
        ({"concurrency": 0}, {}, ("concurrency",), "greater_than_equal"),
        ({}, {"timeout_seconds": 0}, ("nodes", 0, "timeout_seconds"), "greater_than"),
        ({}, {"retries": -1}, ("nodes", 0, "retries"), "greater_than_equal"),
        ({}, {"retry_backoff_seconds": -0.1}, ("nodes", 0, "retry_backoff_seconds"), "greater_than_equal"),
    ],
)
def test_pipeline_validation_rejects_invalid_numeric_runtime_settings(
    pipeline_patch,
    node_patch,
    expected_loc,
    expected_type,
):
    payload = {
        "name": "invalid-runtime-settings",
        "working_dir": ".",
        "nodes": [
            {
                "id": "plan",
                "agent": "codex",
                "prompt": "plan",
                **node_patch,
            }
        ],
        **pipeline_patch,
    }

    with pytest.raises(ValidationError) as exc_info:
        PipelineSpec.model_validate(payload)

    assert exc_info.value.errors()[0]["loc"] == expected_loc
    assert exc_info.value.errors()[0]["type"] == expected_type


@pytest.mark.asyncio
async def test_store_loads_runs_and_artifacts_from_disk(tmp_path):
    pipeline = PipelineSpec.model_validate(
        {
            "name": "persisted",
            "working_dir": str(tmp_path),
            "nodes": [{"id": "alpha", "agent": "codex", "prompt": "hi"}],
        }
    )
    original = RunStore(tmp_path / "runs")
    record = RunRecord(id="run-1", pipeline=pipeline)
    await original.create_run(record)
    await original.append_event("run-1", RunEvent(run_id="run-1", type="run_started"))
    await original.write_artifact_text("run-1", "alpha", "output.txt", "hello persisted")

    reloaded = RunStore(tmp_path / "runs")
    assert reloaded.get_run("run-1").pipeline.name == "persisted"
    assert reloaded.get_events("run-1")[0].type == "run_started"
    assert reloaded.read_artifact_text("run-1", "alpha", "output.txt") == "hello persisted"


def test_store_expands_user_in_base_dir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    store = RunStore("~/agentflow-runs")

    assert store.base_dir == home / "agentflow-runs"
    assert store.base_dir.exists()
