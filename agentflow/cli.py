from __future__ import annotations

import asyncio
import os
import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
import yaml

import typer
from pydantic import ValidationError
from agentflow.defaults import default_smoke_pipeline_path
from agentflow.doctor import (
    DoctorCheck,
    DoctorReport,
    build_bash_login_shell_bridge_recommendation,
    build_pipeline_local_kimi_readiness_checks,
    build_pipeline_local_claude_readiness_checks,
    build_pipeline_local_codex_readiness_checks,
    build_local_smoke_doctor_report,
    build_pipeline_local_codex_auth_checks,
)
from agentflow.local_shell import (
    kimi_shell_init_requires_bash_warning,
    kimi_shell_init_requires_interactive_bash_warning,
    shell_command_prefixes_env_var,
    shell_command_uses_kimi_helper,
    shell_init_exports_env_var,
    shell_init_uses_kimi_helper,
    shell_template_exports_env_var_before_command,
    target_bash_home,
    target_uses_interactive_bash,
    target_uses_login_bash,
)
from agentflow.specs import AgentKind, LocalTarget, provider_uses_kimi_anthropic_auth, resolve_provider

app = typer.Typer(add_completion=False)


class StructuredOutputFormat(StrEnum):
    AUTO = "auto"
    JSON = "json"
    JSON_SUMMARY = "json-summary"
    SUMMARY = "summary"


class InspectionOutputFormat(StrEnum):
    AUTO = "auto"
    JSON = "json"
    JSON_SUMMARY = "json-summary"
    SUMMARY = "summary"


class RunOutputFormat(StrEnum):
    AUTO = "auto"
    JSON = "json"
    JSON_SUMMARY = "json-summary"
    SUMMARY = "summary"


class SmokePreflightMode(StrEnum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


_KIMI_SHELL_PREFLIGHT_AGENTS = {"codex", "claude", "kimi"}


def _build_runtime(runs_dir: str, max_concurrent_runs: int) -> tuple[object, object]:
    from agentflow.orchestrator import Orchestrator
    from agentflow.store import RunStore

    store = RunStore(runs_dir)
    orchestrator = Orchestrator(store=store, max_concurrent_runs=max_concurrent_runs)
    return store, orchestrator


def _build_store(runs_dir: str) -> object:
    from agentflow.store import RunStore

    return RunStore(runs_dir)


def _create_web_app(store: object, orchestrator: object) -> object:
    from agentflow.app import create_app

    return create_app(store=store, orchestrator=orchestrator)


def _serve_web_app(web_app: object, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(web_app, host=host, port=port)


def _load_pipeline(path: str) -> object:
    from agentflow.loader import load_pipeline_from_path

    try:
        return load_pipeline_from_path(path)
    except (OSError, ValidationError, ValueError, yaml.YAMLError) as exc:
        typer.echo(f"Failed to load pipeline `{path}`:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc


def _status_value(status: object) -> str:
    return getattr(status, "value", str(status))


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _format_duration(started_at: str | None, finished_at: str | None) -> str | None:
    started = _parse_iso8601(started_at)
    finished = _parse_iso8601(finished_at)
    if started is None or finished is None:
        return None
    duration_seconds = max((finished - started).total_seconds(), 0.0)
    if duration_seconds < 10:
        return f"{duration_seconds:.1f}s"
    if duration_seconds < 60:
        return f"{duration_seconds:.0f}s"
    minutes, seconds = divmod(int(duration_seconds), 60)
    return f"{minutes}m {seconds}s"


def _duration_seconds(started_at: str | None, finished_at: str | None) -> float | None:
    started = _parse_iso8601(started_at)
    finished = _parse_iso8601(finished_at)
    if started is None or finished is None:
        return None
    return max((finished - started).total_seconds(), 0.0)


def _preview_text(text: str | None, *, limit: int = 100) -> str | None:
    if text is None:
        return None
    collapsed = " ".join(text.split())
    if not collapsed:
        return None
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _node_attempt_count(node: object) -> int:
    current_attempt = getattr(node, "current_attempt", 0) or 0
    attempts = getattr(node, "attempts", []) or []
    return current_attempt or len(attempts)


def _provider_name(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        name = value.get("name")
        return str(name) if name else None
    name = getattr(value, "name", None)
    if name:
        return str(name)
    if hasattr(value, "model_dump"):
        data = value.model_dump(mode="json")
        if isinstance(data, dict):
            name = data.get("name")
            if name:
                return str(name)
    return None


def _pipeline_node_map(record: object) -> dict[str, object]:
    pipeline_nodes = getattr(getattr(record, "pipeline", None), "nodes", None) or []
    return {
        node_id: node
        for node in pipeline_nodes
        if (node_id := getattr(node, "id", None))
    }


def _node_identity(node_id: str, pipeline_node: object | None) -> str:
    if pipeline_node is None:
        return node_id

    parts: list[str] = []
    agent = getattr(pipeline_node, "agent", None)
    if agent is not None:
        parts.append(_status_value(agent))

    model = getattr(pipeline_node, "model", None)
    if model:
        parts.append(f"model={model}")

    provider = _provider_name(getattr(pipeline_node, "provider", None))
    if provider:
        parts.append(f"provider={provider}")

    if not parts:
        return node_id
    return f"{node_id} [{', '.join(parts)}]"


def _node_preview(node: object) -> str | None:
    for candidate in (getattr(node, "final_response", None), getattr(node, "output", None)):
        preview = _preview_text(candidate)
        if preview is not None:
            return preview
    stderr_lines = getattr(node, "stderr_lines", []) or []
    if stderr_lines:
        return _preview_text(stderr_lines[-1])
    return None


def _build_run_summary(record: object, run_dir: Path | str | None = None) -> dict[str, object]:
    summary: dict[str, object] = {
        "id": record.id,
        "status": _status_value(record.status),
        "nodes": [],
    }
    pipeline_name = getattr(getattr(record, "pipeline", None), "name", None)
    if pipeline_name:
        summary["pipeline"] = {"name": pipeline_name}
    started_at = getattr(record, "started_at", None)
    if started_at:
        summary["started_at"] = started_at
    finished_at = getattr(record, "finished_at", None)
    if finished_at:
        summary["finished_at"] = finished_at
    duration = _format_duration(started_at, finished_at)
    if duration is not None:
        summary["duration"] = duration
    duration_seconds = _duration_seconds(started_at, finished_at)
    if duration_seconds is not None:
        summary["duration_seconds"] = duration_seconds
    if run_dir is not None:
        summary["run_dir"] = str(run_dir)

    nodes: list[dict[str, object]] = []
    pipeline_nodes = _pipeline_node_map(record)
    for node_id, node in (getattr(record, "nodes", {}) or {}).items():
        pipeline_node = pipeline_nodes.get(node_id)
        node_summary: dict[str, object] = {
            "id": node_id,
            "status": _status_value(getattr(node, "status", "unknown")),
        }
        if pipeline_node is not None:
            agent = getattr(pipeline_node, "agent", None)
            if agent is not None:
                node_summary["agent"] = _status_value(agent)
            model = getattr(pipeline_node, "model", None)
            if model:
                node_summary["model"] = model
            provider = _provider_name(getattr(pipeline_node, "provider", None))
            if provider:
                node_summary["provider"] = provider
        attempts = _node_attempt_count(node)
        if attempts:
            node_summary["attempts"] = attempts
        exit_code = getattr(node, "exit_code", None)
        if exit_code is not None:
            node_summary["exit_code"] = exit_code
        preview = _node_preview(node)
        if preview is not None:
            node_summary["preview"] = preview
        nodes.append(node_summary)

    summary["nodes"] = nodes
    return summary


def _render_run_summary(record: object, run_dir: Path | str | None = None) -> str:
    summary = _build_run_summary(record, run_dir=run_dir)
    lines = [f"Run {summary['id']}: {summary['status']}"]
    pipeline = summary.get("pipeline")
    if isinstance(pipeline, dict) and pipeline.get("name"):
        lines.append(f"Pipeline: {pipeline['name']}")
    duration = summary.get("duration")
    if duration is not None:
        lines.append(f"Duration: {duration}")
    run_dir_value = summary.get("run_dir")
    if run_dir_value is not None:
        lines.append(f"Run dir: {run_dir_value}")
    nodes = summary.get("nodes")
    if isinstance(nodes, list) and nodes:
        lines.append("Nodes:")
        for node in nodes:
            node_id = str(node["id"])
            parts: list[str] = []
            agent = node.get("agent")
            if agent is not None:
                parts.append(str(agent))
            model = node.get("model")
            if model:
                parts.append(f"model={model}")
            provider = node.get("provider")
            if provider:
                parts.append(f"provider={provider}")
            identity = node_id if not parts else f"{node_id} [{', '.join(parts)}]"
            rendered = f"{identity}: {node['status']}"
            metadata: list[str] = []
            attempts = node.get("attempts")
            if attempts:
                metadata.append(f"attempt {attempts}")
            exit_code = node.get("exit_code")
            if exit_code is not None:
                metadata.append(f"exit {exit_code}")
            if metadata:
                rendered += f" ({', '.join(metadata)})"
            preview = node.get("preview")
            if preview is not None:
                rendered += f" - {preview}"
            lines.append(f"- {rendered}")
    return "\n".join(lines)


def _resolve_run_output(output: RunOutputFormat, *, err: bool = False) -> RunOutputFormat:
    if output != RunOutputFormat.AUTO:
        return output
    if _stream_supports_tty_summary(err=err):
        return RunOutputFormat.SUMMARY
    return RunOutputFormat.JSON


def _echo_run_result(record: object, *, output: RunOutputFormat, run_dir: Path | str | None = None) -> None:
    resolved_output = _resolve_run_output(output)
    if resolved_output == RunOutputFormat.SUMMARY:
        typer.echo(_render_run_summary(record, run_dir=run_dir))
        return
    if resolved_output == RunOutputFormat.JSON_SUMMARY:
        typer.echo(json.dumps(_build_run_summary(record, run_dir=run_dir), indent=2))
        return
    typer.echo(json.dumps(record.model_dump(mode="json"), indent=2))


def _run_dir_for_record(store: object | None, run_id: str) -> Path | str | None:
    if store is None:
        return None
    run_dir = getattr(store, "run_dir", None)
    if not callable(run_dir):
        return None
    try:
        return run_dir(run_id)
    except Exception:
        return None


def _build_runs_summary(records: list[object], *, store: object | None = None) -> list[dict[str, object]]:
    return [
        _build_run_summary(record, run_dir=_run_dir_for_record(store, getattr(record, "id", "")))
        for record in records
    ]


def _render_runs_summary(records: list[object], *, store: object | None = None, total: int | None = None) -> str:
    summaries = _build_runs_summary(records, store=store)
    if not summaries:
        return "No runs found."

    visible_count = len(summaries)
    total_count = visible_count if total is None else total
    header = f"Runs: {visible_count}" if total_count == visible_count else f"Runs: {visible_count} of {total_count}"
    lines = [header]
    for summary in summaries:
        rendered = f"- {summary['id']}: {summary['status']}"
        pipeline = summary.get("pipeline")
        if isinstance(pipeline, dict) and pipeline.get("name"):
            rendered += f" - {pipeline['name']}"
        duration = summary.get("duration")
        if duration is not None:
            rendered += f" ({duration})"
        lines.append(rendered)
    return "\n".join(lines)


def _echo_runs_result(records: list[object], *, store: object | None, output: RunOutputFormat, total: int | None = None) -> None:
    resolved_output = _resolve_run_output(output)
    if resolved_output == RunOutputFormat.SUMMARY:
        typer.echo(_render_runs_summary(records, store=store, total=total))
        return
    if resolved_output == RunOutputFormat.JSON_SUMMARY:
        typer.echo(json.dumps(_build_runs_summary(records, store=store), indent=2))
        return

    payload: list[object] = []
    for record in records:
        model_dump = getattr(record, "model_dump", None)
        if callable(model_dump):
            payload.append(model_dump(mode="json"))
            continue
        payload.append(_build_run_summary(record, run_dir=_run_dir_for_record(store, getattr(record, "id", ""))))
    typer.echo(json.dumps(payload, indent=2))


def _get_run_or_exit(store: object, run_id: str, *, runs_dir: str) -> object:
    try:
        return store.get_run(run_id)
    except KeyError as exc:
        typer.echo(f"Run `{run_id}` not found in `{runs_dir}`.", err=True)
        raise typer.Exit(code=1) from exc


def _run_pipeline(pipeline: object, runs_dir: str, max_concurrent_runs: int, output: RunOutputFormat) -> None:
    store, orchestrator = _build_runtime(runs_dir, max_concurrent_runs)

    async def _run() -> None:
        run_record = await orchestrator.submit(pipeline)
        completed = await orchestrator.wait(run_record.id, timeout=None)
        run_dir = store.run_dir(run_record.id) if hasattr(store, "run_dir") else None
        _echo_run_result(completed, output=output, run_dir=run_dir)
        raise typer.Exit(code=0 if _status_value(completed.status) == "completed" else 1)

    asyncio.run(_run())


def _run_pipeline_path(path: str, runs_dir: str, max_concurrent_runs: int, output: RunOutputFormat) -> None:
    _run_pipeline(_load_pipeline(path), runs_dir, max_concurrent_runs, output)


def _doctor_report():
    return build_local_smoke_doctor_report()


def _empty_doctor_report() -> DoctorReport:
    return DoctorReport(status="ok", checks=[])


def _path_matches_bundled_smoke(path: str) -> bool:
    return Path(path).expanduser().resolve() == Path(default_smoke_pipeline_path()).expanduser().resolve()


def _extend_doctor_report(report: object, extra_checks: list[DoctorCheck]) -> object:
    if not extra_checks:
        return report

    current_checks = list(getattr(report, "checks", []) or [])
    current_status = _status_value(getattr(report, "status", "ok"))
    next_status = _merge_doctor_status(current_status, extra_checks)
    return replace(report, status=next_status, checks=[*current_checks, *extra_checks])


def _pipeline_launch_inspection_nodes(pipeline: object) -> list[dict[str, object]]:
    from agentflow.inspection import build_launch_inspection

    try:
        report = build_launch_inspection(
            pipeline,
            runs_dir=str((Path.cwd() / ".agentflow" / "doctor").resolve()),
        )
    except Exception:
        return []

    nodes = report.get("nodes")
    if not isinstance(nodes, list):
        return []

    return [node for node in nodes if isinstance(node, dict)]


def _pipeline_launch_env_override_checks(nodes: list[dict[str, object]]) -> list[DoctorCheck]:
    
    checks: list[DoctorCheck] = []
    for node in nodes:
        node_id = str(node.get("id") or "node")
        for override in node.get("launch_env_overrides", []) or []:
            if not isinstance(override, dict):
                continue
            key = str(override.get("key") or "")
            if not key:
                continue

            source = override.get("source")
            source_label = f" via `{source}`"
            if source == "provider.api_key_env":
                source_env_key = override.get("source_env_key")
                if isinstance(source_env_key, str) and source_env_key:
                    source_label = f" via `provider.api_key_env` (`{source_env_key}`)"
            if not isinstance(source, str) or not source:
                source_label = ""

            status = "warning"
            if source in {
                "node.env",
                "provider.env",
                "provider.base_url",
                "provider.headers",
                "provider.api_key_env",
            }:
                status = "ok"

            if status == "ok":
                detail = f"Node `{node_id}`: Launch env uses configured `{key}` for this node{source_label}."
            else:
                detail = f"Node `{node_id}`: Launch env overrides current `{key}` for this node{source_label}."
            if not override.get("redacted"):
                current_value = override.get("current_value")
                launch_value = override.get("launch_value")
                if isinstance(current_value, str) and isinstance(launch_value, str):
                    if not launch_value.strip():
                        detail = (
                            f"Node `{node_id}`: Launch env clears current `{key}` value `{current_value}`"
                            f"{source_label}."
                        )
                    elif status == "ok":
                        detail = (
                            f"Node `{node_id}`: Launch env uses configured `{key}` value `{launch_value}` "
                            f"instead of current `{current_value}`{source_label}."
                        )
                    else:
                        detail = (
                            f"Node `{node_id}`: Launch env overrides current `{key}` from `{current_value}` "
                            f"to `{launch_value}`{source_label}."
                        )

            context = {"node_id": node_id, **override}
            checks.append(
                DoctorCheck(
                    name="launch_env_override",
                    status=status,
                    detail=detail,
                    context=context,
                )
            )
    return checks


def _pipeline_launch_env_inheritance_checks(nodes: list[dict[str, object]]) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for node in nodes:
        node_id = str(node.get("id") or "node")
        agent_name = str(node.get("agent") or "agent").capitalize()
        for inheritance in node.get("launch_env_inheritances", []) or []:
            if not isinstance(inheritance, dict):
                continue
            key = str(inheritance.get("key") or "")
            current_value = str(inheritance.get("current_value") or "")
            if not key or not current_value:
                continue

            detail = (
                f"Node `{node_id}`: Launch inherits current `{key}` value `{current_value}`; configure `provider` "
                f"or `node.env` explicitly if you want {agent_name} routing pinned for this node."
            )
            checks.append(
                DoctorCheck(
                    name="launch_env_inheritance",
                    status="warning",
                    detail=detail,
                    context={"node_id": node_id, **inheritance},
                )
            )
    return checks


def _doctor_report_for_path(path: str | None = None) -> tuple[object, dict[str, object] | None, object | None]:
    report = _doctor_report()
    if path is None:
        try:
            pipeline = _load_pipeline(default_smoke_pipeline_path())
        except typer.Exit:
            return report, None, None
        return _augment_preflight_report(report, pipeline), None, pipeline
    pipeline = _load_pipeline(path)
    if not _path_matches_bundled_smoke(path) and not _pipeline_uses_kimi_smoke_preflight(pipeline):
        report = _empty_doctor_report()
    return _augment_preflight_report(report, pipeline), {"auto_preflight": _auto_smoke_preflight_metadata(path, pipeline)}, pipeline


def _preflight_shell_bridge_recommendation(report: object) -> object | None:
    for check in getattr(report, "checks", []) or []:
        if getattr(check, "name", None) != "bash_login_startup":
            continue
        if _status_value(getattr(check, "status", "unknown")) not in {"warning", "failed"}:
            continue
        return build_bash_login_shell_bridge_recommendation()
    return None


def _doctor_shell_bridge_output(report: object, *, requested: bool) -> tuple[bool, object | None]:
    if requested:
        return True, build_bash_login_shell_bridge_recommendation()

    recommendation = _preflight_shell_bridge_recommendation(report)
    return recommendation is not None, recommendation


def _structured_output_from_run_output(output: RunOutputFormat) -> StructuredOutputFormat:
    resolved_output = _resolve_run_output(output, err=True)
    if resolved_output == RunOutputFormat.SUMMARY:
        return StructuredOutputFormat.SUMMARY
    return StructuredOutputFormat.JSON


def _stream_supports_tty_summary(*, err: bool) -> bool:
    stream = sys.stderr if err else sys.stdout
    isatty = getattr(stream, "isatty", None)
    return bool(callable(isatty) and isatty())


def _resolve_structured_output(output: StructuredOutputFormat, *, err: bool) -> StructuredOutputFormat:
    if output != StructuredOutputFormat.AUTO:
        return output
    if _stream_supports_tty_summary(err=err):
        return StructuredOutputFormat.SUMMARY
    return StructuredOutputFormat.JSON


def _node_uses_kimi_smoke_bootstrap(node: object) -> bool:
    return _node_kimi_smoke_preflight_match(node) is not None


def _node_kimi_shell_bootstrap_check(node: object) -> DoctorCheck | None:
    agent = _status_value(getattr(node, "agent", None)).lower()
    if agent not in _KIMI_SHELL_PREFLIGHT_AGENTS:
        return None

    target = getattr(node, "target", None)
    if getattr(target, "kind", None) != "local":
        return None

    node_id = str(getattr(node, "id", "node"))

    bash_warning = kimi_shell_init_requires_bash_warning(target)
    if bash_warning is not None:
        return DoctorCheck(
            name="kimi_shell_bootstrap",
            status="failed",
            detail=f"Node `{node_id}`: {bash_warning}",
        )

    interactive_warning = kimi_shell_init_requires_interactive_bash_warning(target)
    if interactive_warning is not None:
        return DoctorCheck(
            name="kimi_shell_bootstrap",
            status="warning",
            detail=f"Node `{node_id}`: {interactive_warning}",
        )

    return None


def _node_kimi_smoke_preflight_match(node: object) -> dict[str, str] | None:
    agent = _status_value(getattr(node, "agent", None)).lower()
    if agent not in _KIMI_SHELL_PREFLIGHT_AGENTS:
        return None

    target = getattr(node, "target", None)
    if getattr(target, "kind", None) != "local":
        return None

    node_id = str(getattr(node, "id", None) or agent)

    if str(getattr(target, "bootstrap", "")).strip().lower() == "kimi":
        return {
            "node_id": node_id,
            "agent": agent,
            "trigger": "target.bootstrap",
        }

    shell_init = getattr(target, "shell_init", None)
    if shell_init_uses_kimi_helper(shell_init):
        return {
            "node_id": node_id,
            "agent": agent,
            "trigger": "target.shell_init",
        }

    shell = getattr(target, "shell", None)
    if shell_command_uses_kimi_helper(shell if isinstance(shell, str) else None):
        return {
            "node_id": node_id,
            "agent": agent,
            "trigger": "target.shell",
        }
    return None


def _pipeline_kimi_smoke_preflight_matches(pipeline: object) -> list[dict[str, str]]:
    nodes = getattr(pipeline, "nodes", None) or []
    matches: list[dict[str, str]] = []
    for node in nodes:
        match = _node_kimi_smoke_preflight_match(node)
        if match is not None:
            matches.append(match)
    return matches


def _render_kimi_smoke_preflight_matches(matches: list[dict[str, str]]) -> list[str]:
    rendered: list[str] = []
    for match in matches:
        node_id = match["node_id"]
        agent = match["agent"]
        trigger = match["trigger"]
        rendered.append(f"{node_id} ({agent}) via `{trigger}`")
    return rendered


def _pipeline_uses_kimi_smoke_preflight(pipeline: object) -> bool:
    return bool(_pipeline_kimi_smoke_preflight_matches(pipeline))


def _pipeline_kimi_shell_bootstrap_checks(pipeline: object) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for node in getattr(pipeline, "nodes", None) or []:
        check = _node_kimi_shell_bootstrap_check(node)
        if check is None:
            continue
        checks.append(check)
    return checks


def _target_value(target: object, key: str, default: object | None = None) -> object | None:
    if isinstance(target, dict):
        return target.get(key, default)
    return getattr(target, key, default)


def _coerce_local_target(target: object) -> LocalTarget | None:
    if _status_value(_target_value(target, "kind")).lower() != "local":
        return None

    payload = {
        "kind": "local",
        "cwd": _target_value(target, "cwd"),
        "shell": _target_value(target, "shell"),
        "shell_login": bool(_target_value(target, "shell_login", False)),
        "shell_interactive": bool(_target_value(target, "shell_interactive", False)),
        "shell_init": _target_value(target, "shell_init"),
    }
    return LocalTarget.model_validate(payload)


def _resolved_provider_api_key_env(node: object) -> tuple[str | None, str | None]:
    agent = _status_value(getattr(node, "agent", None)).lower()
    if agent not in {member.value for member in AgentKind}:
        return None, None

    provider = resolve_provider(getattr(node, "provider", None), AgentKind(agent))
    if provider is not None and provider.api_key_env:
        return provider.api_key_env, provider.name
    if agent == AgentKind.KIMI.value:
        return "KIMI_API_KEY", "moonshot"
    return None, None


def _provider_credentials_come_from_local_bootstrap(
    node: object,
    *,
    api_key_env: str,
    provider: object | None,
) -> bool:
    target = _coerce_local_target(getattr(node, "target", None))
    if target is not None:
        effective_home = target_bash_home(target)
        shell_init = getattr(target, "shell_init", None)
        if shell_init_exports_env_var(shell_init, api_key_env, home=effective_home):
            return True

        shell = getattr(target, "shell", None)
        if shell_template_exports_env_var_before_command(
            shell if isinstance(shell, str) else None,
            api_key_env,
            home=effective_home,
        ):
            return True
        if shell_command_prefixes_env_var(shell if isinstance(shell, str) else None, api_key_env):
            return True
        uses_login_bash = target_uses_login_bash(target)
        uses_interactive_bash = target_uses_interactive_bash(target)
        if uses_login_bash or uses_interactive_bash:
            env = os.environ.copy()
            env["HOME"] = str(effective_home)
            bash_flag = "-"
            if uses_login_bash:
                bash_flag += "l"
            if uses_interactive_bash:
                bash_flag += "i"
            bash_flag += "c"
            try:
                result = subprocess.run(
                    ["bash", bash_flag, f'test -n "${{{api_key_env}:-}}"'],
                    check=False,
                    capture_output=True,
                    env=env,
                    text=True,
                )
            except OSError:
                result = None
            if result is not None and result.returncode == 0:
                return True

    if api_key_env == "ANTHROPIC_API_KEY" and provider_uses_kimi_anthropic_auth(provider):
        return _node_uses_kimi_smoke_bootstrap(node)
    return False


def _pipeline_provider_credential_checks(pipeline: object) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for node in getattr(pipeline, "nodes", None) or []:
        node_id = str(getattr(node, "id", "node"))
        api_key_env, provider_name = _resolved_provider_api_key_env(node)
        if not api_key_env:
            continue

        node_env = getattr(node, "env", None) or {}
        provider = resolve_provider(getattr(node, "provider", None), AgentKind(_status_value(getattr(node, "agent", None)).lower()))
        provider_env = getattr(provider, "env", None) or {}
        has_key = any(
            isinstance(source, dict) and str(source.get(api_key_env, "")).strip()
            for source in (node_env, provider_env)
        ) or bool(str(os.getenv(api_key_env, "")).strip())
        if not has_key and _provider_credentials_come_from_local_bootstrap(
            node,
            api_key_env=api_key_env,
            provider=provider,
        ):
            has_key = True
        if has_key:
            continue

        agent = _status_value(getattr(node, "agent", None)).lower()
        provider_detail = f" provider `{provider_name}`" if provider_name else ""
        checks.append(
            DoctorCheck(
                name="provider_credentials",
                status="failed",
                detail=(
                    f"Node `{node_id}` ({agent}) requires `{api_key_env}` for{provider_detail}, but it is not set in "
                    "the current environment, `node.env`, or `provider.env`."
                ),
            )
        )
    return checks


def _merge_doctor_status(current_status: str, extra_checks: list[DoctorCheck]) -> str:
    statuses = {current_status, *(_status_value(check.status) for check in extra_checks)}
    if "failed" in statuses:
        return "failed"
    if "warning" in statuses:
        return "warning"
    return current_status


def _augment_preflight_report(report: object, pipeline: object) -> object:
    report = _extend_doctor_report(
        report,
        [
        *_pipeline_kimi_shell_bootstrap_checks(pipeline),
        *_pipeline_provider_credential_checks(pipeline),
        *build_pipeline_local_kimi_readiness_checks(pipeline),
        *build_pipeline_local_claude_readiness_checks(pipeline),
        *build_pipeline_local_codex_readiness_checks(pipeline),
        *build_pipeline_local_codex_auth_checks(pipeline),
        ],
    )
    if _status_value(getattr(report, "status", "ok")) == "failed":
        return report

    inspection_nodes = _pipeline_launch_inspection_nodes(pipeline)
    return _extend_doctor_report(
        report,
        [
            *_pipeline_launch_env_override_checks(inspection_nodes),
            *_pipeline_launch_env_inheritance_checks(inspection_nodes),
        ],
    )


def _auto_smoke_preflight_reason(path: str, pipeline: object) -> str | None:
    if _path_matches_bundled_smoke(path):
        return "path matches the bundled real-agent smoke pipeline."
    if _pipeline_uses_kimi_smoke_preflight(pipeline):
        return "local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap."
    return None


def _auto_smoke_preflight_metadata(path: str, pipeline: object) -> dict[str, object]:
    matches = _pipeline_kimi_smoke_preflight_matches(pipeline)
    match_summary = _render_kimi_smoke_preflight_matches(matches)
    reason = _auto_smoke_preflight_reason(path, pipeline)
    if reason is not None:
        return {
            "enabled": True,
            "reason": reason,
            "matches": matches,
            "match_summary": match_summary,
        }
    return {
        "enabled": False,
        "reason": "path does not match the bundled smoke pipeline and no local Codex/Claude/Kimi node uses `kimi` bootstrap.",
        "matches": matches,
        "match_summary": match_summary,
    }


def _should_run_smoke_preflight(
    path: str | None,
    preflight: SmokePreflightMode,
    *,
    pipeline: object | None = None,
) -> bool:
    if preflight == SmokePreflightMode.ALWAYS:
        return True
    if preflight == SmokePreflightMode.NEVER:
        return False
    if path is None:
        return True
    if _path_matches_bundled_smoke(path):
        return True
    if pipeline is None:
        return False
    return _pipeline_uses_kimi_smoke_preflight(pipeline)


def _load_pipeline_with_optional_smoke_preflight(
    path: str | None,
    selected_path: str,
    preflight: SmokePreflightMode,
    output: RunOutputFormat,
    *,
    show_preflight: bool = False,
) -> object:
    pipeline = None
    should_run_preflight = _should_run_smoke_preflight(path, preflight)
    selected_path_matches_bundled = (
        Path(selected_path).expanduser().resolve() == Path(default_smoke_pipeline_path()).expanduser().resolve()
    )
    if not should_run_preflight and preflight == SmokePreflightMode.AUTO and path is not None:
        pipeline = _load_pipeline(selected_path)
        should_run_preflight = _should_run_smoke_preflight(path, preflight, pipeline=pipeline)

    if should_run_preflight:
        if pipeline is None and preflight == SmokePreflightMode.ALWAYS and not selected_path_matches_bundled:
            pipeline = _load_pipeline(selected_path)
        preflight_pipeline = pipeline
        report = _doctor_report()
        if pipeline is not None:
            report = _augment_preflight_report(report, pipeline)
        elif selected_path_matches_bundled and _status_value(getattr(report, "status", "ok")) != "failed":
            preflight_pipeline = _load_pipeline(selected_path)
            report = _augment_preflight_report(report, preflight_pipeline)
        doctor_output = _structured_output_from_run_output(output)
        shell_bridge = _preflight_shell_bridge_recommendation(report)
        include_shell_bridge = shell_bridge is not None
        preflight_context = None
        if preflight_pipeline is not None:
            preflight_context = {
                "auto_preflight": _auto_smoke_preflight_metadata(path or selected_path, preflight_pipeline)
            }
        if report.status == "failed":
            _echo_doctor_report(
                report,
                output=doctor_output,
                include_shell_bridge=include_shell_bridge,
                shell_bridge=shell_bridge,
                pipeline=preflight_context,
            )
            raise typer.Exit(code=1)
        if report.status == "warning":
            _echo_doctor_report(
                report,
                output=doctor_output,
                err=True,
                include_shell_bridge=include_shell_bridge,
                shell_bridge=shell_bridge,
                pipeline=preflight_context,
            )
        elif show_preflight:
            _echo_doctor_report(
                report,
                output=StructuredOutputFormat.SUMMARY,
                err=True,
                include_shell_bridge=include_shell_bridge,
                shell_bridge=shell_bridge,
                pipeline=preflight_context,
            )
        if preflight_pipeline is not None:
            pipeline = preflight_pipeline

    return pipeline if pipeline is not None else _load_pipeline(selected_path)


def _render_shell_bridge_summary(shell_bridge: object | None) -> str:
    if shell_bridge is None:
        return "Shell bridge suggestion: not needed"

    return "\n".join(
        [
            (
                f"Shell bridge suggestion for `{getattr(shell_bridge, 'target', '~/.profile')}` "
                f"from `{getattr(shell_bridge, 'source', '~/.bashrc')}`:"
            ),
            f"Reason: {getattr(shell_bridge, 'reason', '')}",
            getattr(shell_bridge, "snippet", "").rstrip(),
        ]
    )


def _render_doctor_summary(
    report: object,
    *,
    include_shell_bridge: bool = False,
    shell_bridge: object | None = None,
    pipeline: dict[str, object] | None = None,
) -> str:
    lines = [f"Doctor: {_status_value(getattr(report, 'status', 'unknown'))}"]
    for check in getattr(report, "checks", []) or []:
        lines.append(
            f"- {getattr(check, 'name', 'unknown')}: {_status_value(getattr(check, 'status', 'unknown'))}"
            f" - {getattr(check, 'detail', '')}"
        )
    raw_auto_preflight = pipeline.get("auto_preflight") if isinstance(pipeline, dict) else None
    if isinstance(raw_auto_preflight, dict):
        enabled = raw_auto_preflight.get("enabled")
        reason = raw_auto_preflight.get("reason")
        if isinstance(reason, str) and reason:
            status = "enabled" if enabled else "disabled"
            lines.append(f"Pipeline auto preflight: {status} - {reason}")
        matches = raw_auto_preflight.get("match_summary")
        if isinstance(matches, list):
            rendered_matches = [match for match in matches if isinstance(match, str) and match]
            if rendered_matches:
                lines.append(f"Pipeline auto preflight matches: {', '.join(rendered_matches)}")
    if include_shell_bridge:
        lines.append(_render_shell_bridge_summary(shell_bridge))
    return "\n".join(lines)


def _build_doctor_payload(
    report: object,
    *,
    include_shell_bridge: bool = False,
    shell_bridge: object | None = None,
    pipeline: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = report.as_dict()
    if pipeline is not None:
        payload["pipeline"] = dict(pipeline)
    if include_shell_bridge:
        payload["shell_bridge"] = None if shell_bridge is None else shell_bridge.as_dict()
    return payload


def _echo_doctor_report(
    report: object,
    *,
    output: StructuredOutputFormat = StructuredOutputFormat.JSON,
    err: bool = False,
    include_shell_bridge: bool = False,
    shell_bridge: object | None = None,
    pipeline: dict[str, object] | None = None,
) -> None:
    resolved_output = _resolve_structured_output(output, err=err)
    if resolved_output == StructuredOutputFormat.SUMMARY:
        typer.echo(
            _render_doctor_summary(
                report,
                include_shell_bridge=include_shell_bridge,
                shell_bridge=shell_bridge,
                pipeline=pipeline,
            ),
            err=err,
        )
        return
    typer.echo(
        json.dumps(
            _build_doctor_payload(
                report,
                include_shell_bridge=include_shell_bridge,
                shell_bridge=shell_bridge,
                pipeline=pipeline,
            ),
            indent=2,
        ),
        err=err,
    )


def _echo_inspection(report: dict[str, object], *, output: InspectionOutputFormat) -> None:
    from agentflow.inspection import build_launch_inspection_summary

    resolved_output = _resolve_inspection_output(output)

    if resolved_output == InspectionOutputFormat.SUMMARY:
        from agentflow.inspection import render_launch_inspection_summary

        typer.echo(render_launch_inspection_summary(report))
        return
    if resolved_output == InspectionOutputFormat.JSON_SUMMARY:
        typer.echo(json.dumps(build_launch_inspection_summary(report), indent=2))
        return
    typer.echo(json.dumps(report, indent=2))


def _resolve_inspection_output(output: InspectionOutputFormat) -> InspectionOutputFormat:
    if output != InspectionOutputFormat.AUTO:
        return output
    if _stream_supports_tty_summary(err=False):
        return InspectionOutputFormat.SUMMARY
    return InspectionOutputFormat.JSON


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    runs_dir: str = typer.Option(".agentflow/runs", envvar="AGENTFLOW_RUNS_DIR"),
    max_concurrent_runs: int = typer.Option(2, envvar="AGENTFLOW_MAX_CONCURRENT_RUNS"),
) -> None:
    store, orchestrator = _build_runtime(runs_dir, max_concurrent_runs)
    _serve_web_app(_create_web_app(store=store, orchestrator=orchestrator), host=host, port=port)


@app.command()
def validate(path: str) -> None:
    pipeline = _load_pipeline(path)
    typer.echo(json.dumps(pipeline.model_dump(mode="json"), indent=2))


@app.command()
def runs(
    runs_dir: str = typer.Option(".agentflow/runs", envvar="AGENTFLOW_RUNS_DIR"),
    output: RunOutputFormat = typer.Option(RunOutputFormat.SUMMARY, "--output", help="Result output format."),
    limit: int = typer.Option(20, min=0, help="Maximum runs to show. Use `0` to show all persisted runs."),
) -> None:
    store = _build_store(runs_dir)
    all_runs = store.list_runs()
    selected_runs = all_runs if limit == 0 else all_runs[:limit]
    _echo_runs_result(selected_runs, store=store, output=output, total=len(all_runs))


@app.command()
def show(
    run_id: str,
    runs_dir: str = typer.Option(".agentflow/runs", envvar="AGENTFLOW_RUNS_DIR"),
    output: RunOutputFormat = typer.Option(RunOutputFormat.SUMMARY, "--output", help="Result output format."),
) -> None:
    store = _build_store(runs_dir)
    record = _get_run_or_exit(store, run_id, runs_dir=runs_dir)
    _echo_run_result(record, output=output, run_dir=_run_dir_for_record(store, run_id))


@app.command()
def cancel(
    run_id: str,
    runs_dir: str = typer.Option(".agentflow/runs", envvar="AGENTFLOW_RUNS_DIR"),
    max_concurrent_runs: int = typer.Option(2, envvar="AGENTFLOW_MAX_CONCURRENT_RUNS"),
    output: RunOutputFormat = typer.Option(RunOutputFormat.SUMMARY, "--output", help="Result output format."),
) -> None:
    store, orchestrator = _build_runtime(runs_dir, max_concurrent_runs)

    async def _cancel() -> None:
        try:
            record = await orchestrator.cancel(run_id)
        except KeyError as exc:
            typer.echo(f"Run `{run_id}` not found in `{runs_dir}`.", err=True)
            raise typer.Exit(code=1) from exc
        _echo_run_result(record, output=output, run_dir=_run_dir_for_record(store, record.id))

    asyncio.run(_cancel())


@app.command()
def rerun(
    run_id: str,
    runs_dir: str = typer.Option(".agentflow/runs", envvar="AGENTFLOW_RUNS_DIR"),
    max_concurrent_runs: int = typer.Option(2, envvar="AGENTFLOW_MAX_CONCURRENT_RUNS"),
    output: RunOutputFormat = typer.Option(RunOutputFormat.SUMMARY, "--output", help="Result output format."),
) -> None:
    store, orchestrator = _build_runtime(runs_dir, max_concurrent_runs)

    async def _rerun() -> None:
        try:
            record = await orchestrator.rerun(run_id)
        except KeyError as exc:
            typer.echo(f"Run `{run_id}` not found in `{runs_dir}`.", err=True)
            raise typer.Exit(code=1) from exc
        completed = await orchestrator.wait(record.id, timeout=None)
        _echo_run_result(completed, output=output, run_dir=_run_dir_for_record(store, record.id))
        raise typer.Exit(code=0 if _status_value(completed.status) == "completed" else 1)

    asyncio.run(_rerun())


@app.command()
def inspect(
    path: str,
    node: list[str] = typer.Option(None, "--node", "-n", help="Inspect only the selected node ids."),
    runs_dir: str = typer.Option(".agentflow/runs", envvar="AGENTFLOW_RUNS_DIR"),
    output: InspectionOutputFormat = typer.Option(
        InspectionOutputFormat.AUTO,
        "--output",
        help="Result output format. Defaults to `summary` on a terminal and `json` otherwise.",
    ),
) -> None:
    from agentflow.inspection import build_launch_inspection

    pipeline = _load_pipeline(path)
    try:
        report = build_launch_inspection(pipeline, runs_dir=runs_dir, node_ids=node or None)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--node") from exc
    report.setdefault("pipeline", {})["auto_preflight"] = _auto_smoke_preflight_metadata(path, pipeline)
    _echo_inspection(report, output=output)


@app.command()
def run(
    path: str,
    runs_dir: str = typer.Option(".agentflow/runs", envvar="AGENTFLOW_RUNS_DIR"),
    max_concurrent_runs: int = typer.Option(2, envvar="AGENTFLOW_MAX_CONCURRENT_RUNS"),
    output: RunOutputFormat = typer.Option(
        RunOutputFormat.AUTO,
        "--output",
        help="Result output format. Defaults to `summary` on a terminal and `json` otherwise.",
    ),
    preflight: SmokePreflightMode = typer.Option(
        SmokePreflightMode.AUTO,
        "--preflight",
        help="When to run the local smoke preflight for bundled or Kimi-bootstrapped local pipelines.",
    ),
    show_preflight: bool = typer.Option(
        False,
        "--show-preflight",
        help="Print a successful local preflight summary to stderr when preflight runs.",
    ),
) -> None:
    pipeline = _load_pipeline_with_optional_smoke_preflight(
        path,
        path,
        preflight,
        output,
        show_preflight=show_preflight,
    )
    _run_pipeline(pipeline, runs_dir, max_concurrent_runs, output)


@app.command()
def smoke(
    path: str | None = typer.Argument(None, help="Optional pipeline path. Defaults to the bundled real-agent smoke example."),
    runs_dir: str = typer.Option(".agentflow/runs", envvar="AGENTFLOW_RUNS_DIR"),
    max_concurrent_runs: int = typer.Option(2, envvar="AGENTFLOW_MAX_CONCURRENT_RUNS"),
    output: RunOutputFormat = typer.Option(RunOutputFormat.SUMMARY, "--output", help="Result output format."),
    preflight: SmokePreflightMode = typer.Option(
        SmokePreflightMode.AUTO,
        "--preflight",
        help="When to run the local smoke preflight for bundled or Kimi-bootstrapped local pipelines.",
    ),
    show_preflight: bool = typer.Option(
        False,
        "--show-preflight",
        help="Print a successful local preflight summary to stderr when preflight runs.",
    ),
) -> None:
    selected_path = path or default_smoke_pipeline_path()
    pipeline = _load_pipeline_with_optional_smoke_preflight(
        path,
        selected_path,
        preflight,
        output,
        show_preflight=show_preflight,
    )
    _run_pipeline(pipeline, runs_dir, max_concurrent_runs, output)


@app.command("check-local")
def check_local(
    path: str | None = typer.Argument(None, help="Optional pipeline path. Defaults to the bundled real-agent smoke example."),
    runs_dir: str = typer.Option(".agentflow/runs", envvar="AGENTFLOW_RUNS_DIR"),
    max_concurrent_runs: int = typer.Option(2, envvar="AGENTFLOW_MAX_CONCURRENT_RUNS"),
    output: RunOutputFormat = typer.Option(RunOutputFormat.SUMMARY, "--output", help="Result output format."),
    shell_bridge: bool = typer.Option(
        False,
        "--shell-bridge",
        help="Include a ready-to-paste bash login bridge suggestion when local shell startup needs one.",
    ),
) -> None:
    selected_path = path or default_smoke_pipeline_path()
    report, pipeline, _loaded_pipeline = _doctor_report_for_path(selected_path)
    include_shell_bridge, recommendation = _doctor_shell_bridge_output(report, requested=shell_bridge)
    doctor_output = _structured_output_from_run_output(output)
    _echo_doctor_report(
        report,
        output=doctor_output,
        err=True,
        include_shell_bridge=include_shell_bridge,
        shell_bridge=recommendation,
        pipeline=pipeline,
    )
    if report.status == "failed":
        raise typer.Exit(code=1)
    _run_pipeline(_loaded_pipeline if _loaded_pipeline is not None else _load_pipeline(selected_path), runs_dir, max_concurrent_runs, output)


@app.command()
def doctor(
    path: str | None = typer.Argument(
        None,
        help="Optional pipeline path. Adds pipeline-specific local shell bootstrap warnings to the doctor report.",
    ),
    output: StructuredOutputFormat = typer.Option(
        StructuredOutputFormat.AUTO,
        "--output",
        help="Result output format. Defaults to `summary` on a terminal and `json` otherwise.",
    ),
    shell_bridge: bool = typer.Option(
        False,
        "--shell-bridge",
        help="Include a ready-to-paste bash login bridge suggestion when local shell startup needs one.",
    ),
) -> None:
    report, pipeline, _loaded_pipeline = _doctor_report_for_path(path)
    include_shell_bridge, recommendation = _doctor_shell_bridge_output(report, requested=shell_bridge)
    _echo_doctor_report(
        report,
        output=output,
        include_shell_bridge=include_shell_bridge,
        shell_bridge=recommendation,
        pipeline=pipeline,
    )
    raise typer.Exit(code=0 if report.status != "failed" else 1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
