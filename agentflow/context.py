from __future__ import annotations

from pathlib import Path
from typing import Any

from agentflow.skills import compile_skill_prelude
from agentflow.specs import NodeResult, NodeSpec, NodeStatus, PipelineSpec
from agentflow.utils import render_template


def _artifact_paths_context(*, run_id: str, artifacts_base_dir: Path, node_id: str) -> dict[str, Any]:
    artifact_dir = artifacts_base_dir.expanduser().resolve() / run_id / "artifacts" / node_id
    return {
        "directory": str(artifact_dir),
        "stdout_log": str(artifact_dir / "stdout.log"),
        "stderr_log": str(artifact_dir / "stderr.log"),
        "trace_jsonl": str(artifact_dir / "trace.jsonl"),
        "output_txt": str(artifact_dir / "output.txt"),
        "result_json": str(artifact_dir / "result.json"),
        "launch_json": str(artifact_dir / "launch.json"),
    }


def _node_result_context(
    result: NodeResult,
    *,
    run_id: str | None = None,
    artifacts_base_dir: Path | None = None,
) -> dict[str, Any]:
    context = {
        "status": result.status.value,
        "output": result.output,
        "final_response": result.final_response,
        "stdout": "\n".join(result.stdout_lines),
        "stderr": "\n".join(result.stderr_lines),
        "trace": [event.model_dump(mode="json") for event in result.trace_events],
        "diff": getattr(result, "diff", ""),
    }
    if run_id is not None and artifacts_base_dir is not None:
        context["artifacts"] = _artifact_paths_context(
            run_id=run_id,
            artifacts_base_dir=artifacts_base_dir,
            node_id=result.node_id,
        )
    return context


def _fanout_subset_context(member_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ids": [member["id"] for member in member_nodes],
        "size": len(member_nodes),
        "nodes": member_nodes,
        "outputs": [member["output"] for member in member_nodes],
        "final_responses": [member["final_response"] for member in member_nodes],
        "statuses": [member["status"] for member in member_nodes],
        "values": [member.get("value") for member in member_nodes],
    }


def _fanout_has_output(member: dict[str, Any]) -> bool:
    output = member.get("output")
    return isinstance(output, str) and bool(output.strip())


def _fanout_context(member_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    subset_context = _fanout_subset_context(member_nodes)
    status_counts = {status.value: 0 for status in NodeStatus}
    for member in member_nodes:
        status_counts[member["status"]] = status_counts.get(member["status"], 0) + 1

    with_output_nodes = [member for member in member_nodes if _fanout_has_output(member)]
    without_output_nodes = [member for member in member_nodes if not _fanout_has_output(member)]
    fanout_context = {
        **subset_context,
        "status_counts": status_counts,
        "summary": {
            "total": len(member_nodes),
            "with_output": len(with_output_nodes),
            "without_output": len(without_output_nodes),
            **status_counts,
        },
        "with_output": _fanout_subset_context(with_output_nodes),
        "without_output": _fanout_subset_context(without_output_nodes),
    }
    for status in NodeStatus:
        fanout_context[status.value] = _fanout_subset_context(
            [member for member in member_nodes if member["status"] == status.value]
        )
    return fanout_context


def _fanout_member_context(
    member_id: str,
    *,
    group_id: str | None,
    pipeline_nodes: dict[str, NodeSpec],
    results: dict[str, NodeResult],
    run_id: str | None = None,
    artifacts_base_dir: Path | None = None,
) -> dict[str, Any]:
    result = results.get(member_id, NodeResult(node_id=member_id))
    member_context = {
        "id": member_id,
        **_node_result_context(
            result,
            run_id=run_id,
            artifacts_base_dir=artifacts_base_dir,
        ),
    }
    pipeline_node = pipeline_nodes.get(member_id)
    if pipeline_node is not None and pipeline_node.fanout_member:
        if group_id is None or pipeline_node.fanout_group == group_id:
            member_context.update(pipeline_node.fanout_member)
    return member_context


def _current_node_context(
    node: NodeSpec,
    *,
    current_tick_number: int | None = None,
    current_tick_started_at: str | None = None,
    current_iteration: int | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "id": node.id,
        "agent": node.agent.value,
        "depends_on": list(node.depends_on),
    }
    if node.fanout_group:
        context["fanout_group"] = node.fanout_group
    if node.fanout_member:
        for key, value in node.fanout_member.items():
            if key in context:
                continue
            context[key] = value
    if node.schedule is not None:
        context["schedule"] = node.schedule.model_dump(mode="json")
    if current_tick_number is not None:
        context["tick_number"] = current_tick_number
    if current_tick_started_at is not None:
        context["tick_started_at"] = current_tick_started_at
    if current_iteration is not None:
        context["iteration"] = current_iteration
    return context


def build_render_context(
    pipeline: PipelineSpec,
    results: dict[str, NodeResult],
    *,
    current_node: NodeSpec | None = None,
    run_id: str | None = None,
    artifacts_base_dir: Path | None = None,
    current_tick_number: int | None = None,
    current_tick_started_at: str | None = None,
) -> dict[str, Any]:
    nodes: dict[str, Any] = {}
    for node_id, result in results.items():
        nodes[node_id] = _node_result_context(
            result,
            run_id=run_id,
            artifacts_base_dir=artifacts_base_dir,
        )

    pipeline_nodes = pipeline.node_map
    fanouts: dict[str, Any] = {}
    fanout_member_contexts: dict[str, dict[str, Any]] = {}
    for group_id, member_ids in pipeline.fanouts.items():
        member_nodes = [
            _fanout_member_context(
                member_id,
                group_id=group_id,
                pipeline_nodes=pipeline_nodes,
                results=results,
                run_id=run_id,
                artifacts_base_dir=artifacts_base_dir,
            )
            for member_id in member_ids
        ]
        fanouts[group_id] = _fanout_context(member_nodes)
        for member in member_nodes:
            fanout_member_contexts[member["id"]] = member
    context = {"pipeline": pipeline.model_dump(mode="json"), "nodes": nodes, "fanouts": fanouts}
    if current_node is not None:
        current_context = _current_node_context(
            current_node,
            current_tick_number=current_tick_number,
            current_tick_started_at=current_tick_started_at,
        )
        member_ids = current_context.get("member_ids")
        if isinstance(member_ids, list):
            scoped_nodes: list[dict[str, Any]] = []
            for member_id in member_ids:
                if not isinstance(member_id, str) or not member_id:
                    continue
                member_context = fanout_member_contexts.get(member_id)
                if member_context is None:
                    member_context = _fanout_member_context(
                        member_id,
                        group_id=None,
                        pipeline_nodes=pipeline_nodes,
                        results=results,
                        run_id=run_id,
                        artifacts_base_dir=artifacts_base_dir,
                    )
                scoped_nodes.append(member_context)
            current_context["scope"] = _fanout_context(scoped_nodes)
        context["item"] = current_context
    return context


def render_node_prompt(
    pipeline: PipelineSpec,
    node: NodeSpec,
    results: dict[str, NodeResult],
    *,
    run_id: str | None = None,
    artifacts_base_dir: Path | None = None,
    current_tick_number: int | None = None,
    current_tick_started_at: str | None = None,
) -> str:
    context = build_render_context(
        pipeline,
        results,
        current_node=node,
        run_id=run_id,
        artifacts_base_dir=artifacts_base_dir,
        current_tick_number=current_tick_number,
        current_tick_started_at=current_tick_started_at,
    )
    prompt = render_template(node.prompt, context)
    skill_prelude = compile_skill_prelude(
        node.skills,
        pipeline.working_path,
        target_skill_policy=node.target_skill_policy,
    )
    if skill_prelude:
        return f"Selected skills:\n{skill_prelude}\n\nTask:\n{prompt}"
    return prompt
