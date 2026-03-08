from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from agentflow.specs import PipelineSpec


def load_pipeline_from_path(path: str | Path) -> PipelineSpec:
    path = Path(path)
    data = path.read_text(encoding="utf-8")
    parsed = _parse_pipeline_text(data)
    if isinstance(parsed, dict):
        parsed = _resolve_file_relative_paths(parsed, path.parent.resolve())
    return PipelineSpec.model_validate(parsed)


def load_pipeline_from_text(data: str) -> PipelineSpec:
    parsed = _parse_pipeline_text(data)
    return PipelineSpec.model_validate(parsed)


def _parse_pipeline_text(data: str) -> Any:
    parsed: Any
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        parsed = yaml.safe_load(data)
    return parsed


def _resolve_file_relative_paths(parsed: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    resolved = dict(parsed)
    working_dir_value = resolved.get("working_dir", ".")
    working_dir = Path(working_dir_value)
    if not working_dir.is_absolute():
        working_dir = (base_dir / working_dir).resolve()
        resolved["working_dir"] = str(working_dir)
    else:
        working_dir = working_dir.resolve()

    local_target_defaults = resolved.get("local_target_defaults")
    if isinstance(local_target_defaults, dict) and local_target_defaults.get("kind", "local") == "local":
        cwd = local_target_defaults.get("cwd")
        if isinstance(cwd, str) and cwd and not Path(cwd).is_absolute():
            updated_local_target_defaults = dict(local_target_defaults)
            updated_local_target_defaults["cwd"] = str((working_dir / cwd).resolve())
            resolved["local_target_defaults"] = updated_local_target_defaults

    nodes: list[Any] = []
    for node in resolved.get("nodes", []):
        if not isinstance(node, dict):
            nodes.append(node)
            continue
        updated = dict(node)
        target = updated.get("target")
        if isinstance(target, dict) and target.get("kind", "local") == "local":
            cwd = target.get("cwd")
            if isinstance(cwd, str) and cwd and not Path(cwd).is_absolute():
                updated_target = dict(target)
                updated_target["cwd"] = str((working_dir / cwd).resolve())
                updated["target"] = updated_target
        nodes.append(updated)
    resolved["nodes"] = nodes
    return resolved
