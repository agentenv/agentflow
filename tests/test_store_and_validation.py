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
