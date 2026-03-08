from __future__ import annotations

import asyncio
import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import typer
from agentflow.defaults import default_smoke_pipeline_path
from agentflow.doctor import build_local_smoke_doctor_report

app = typer.Typer(add_completion=False)


class RunOutputFormat(StrEnum):
    JSON = "json"
    SUMMARY = "summary"


class SmokePreflightMode(StrEnum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


def _build_runtime(runs_dir: str, max_concurrent_runs: int) -> tuple[object, object]:
    from agentflow.orchestrator import Orchestrator
    from agentflow.store import RunStore

    store = RunStore(runs_dir)
    orchestrator = Orchestrator(store=store, max_concurrent_runs=max_concurrent_runs)
    return store, orchestrator


def _create_web_app(store: object, orchestrator: object) -> object:
    from agentflow.app import create_app

    return create_app(store=store, orchestrator=orchestrator)


def _serve_web_app(web_app: object, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(web_app, host=host, port=port)


def _load_pipeline(path: str) -> object:
    from agentflow.loader import load_pipeline_from_path

    return load_pipeline_from_path(path)


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


def _render_run_summary(record: object, run_dir: Path | str | None = None) -> str:
    lines = [f"Run {record.id}: {_status_value(record.status)}"]
    pipeline_name = getattr(getattr(record, "pipeline", None), "name", None)
    if pipeline_name:
        lines.append(f"Pipeline: {pipeline_name}")
    duration = _format_duration(getattr(record, "started_at", None), getattr(record, "finished_at", None))
    if duration is not None:
        lines.append(f"Duration: {duration}")
    if run_dir is not None:
        lines.append(f"Run dir: {run_dir}")
    nodes = getattr(record, "nodes", {}) or {}
    pipeline_nodes = _pipeline_node_map(record)
    if nodes:
        lines.append("Nodes:")
        for node_id, node in nodes.items():
            identity = _node_identity(node_id, pipeline_nodes.get(node_id))
            summary = f"{identity}: {_status_value(getattr(node, 'status', 'unknown'))}"
            metadata: list[str] = []
            attempts = _node_attempt_count(node)
            if attempts:
                metadata.append(f"attempt {attempts}")
            exit_code = getattr(node, "exit_code", None)
            if exit_code is not None:
                metadata.append(f"exit {exit_code}")
            if metadata:
                summary += f" ({', '.join(metadata)})"
            preview = _node_preview(node)
            if preview is not None:
                summary += f" - {preview}"
            lines.append(f"- {summary}")
    return "\n".join(lines)


def _echo_run_result(record: object, *, output: RunOutputFormat, run_dir: Path | str | None = None) -> None:
    if output == RunOutputFormat.SUMMARY:
        typer.echo(_render_run_summary(record, run_dir=run_dir))
        return
    typer.echo(json.dumps(record.model_dump(mode="json"), indent=2))


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


def _node_uses_kimi_smoke_bootstrap(node: object) -> bool:
    agent = _status_value(getattr(node, "agent", None)).lower()
    if agent not in {"codex", "claude"}:
        return False

    target = getattr(node, "target", None)
    if getattr(target, "kind", None) != "local":
        return False

    shell_init = getattr(target, "shell_init", None)
    if isinstance(shell_init, str) and "kimi" in shell_init.lower():
        return True

    shell = getattr(target, "shell", None)
    return isinstance(shell, str) and "kimi" in shell.lower()


def _pipeline_uses_kimi_smoke_preflight(pipeline: object) -> bool:
    nodes = getattr(pipeline, "nodes", None) or []
    return any(_node_uses_kimi_smoke_bootstrap(node) for node in nodes)


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
    if Path(path).expanduser().resolve() == Path(default_smoke_pipeline_path()).expanduser().resolve():
        return True
    if pipeline is None:
        return False
    return _pipeline_uses_kimi_smoke_preflight(pipeline)


def _render_doctor_summary(report: object) -> str:
    lines = [f"Doctor: {_status_value(getattr(report, 'status', 'unknown'))}"]
    for check in getattr(report, "checks", []) or []:
        lines.append(
            f"- {getattr(check, 'name', 'unknown')}: {_status_value(getattr(check, 'status', 'unknown'))}"
            f" - {getattr(check, 'detail', '')}"
        )
    return "\n".join(lines)


def _echo_doctor_report(report: object, *, output: RunOutputFormat = RunOutputFormat.JSON, err: bool = False) -> None:
    if output == RunOutputFormat.SUMMARY:
        typer.echo(_render_doctor_summary(report), err=err)
        return
    typer.echo(json.dumps(report.as_dict(), indent=2), err=err)


def _echo_inspection(report: dict[str, object], *, output: RunOutputFormat) -> None:
    if output == RunOutputFormat.SUMMARY:
        from agentflow.inspection import render_launch_inspection_summary

        typer.echo(render_launch_inspection_summary(report))
        return
    typer.echo(json.dumps(report, indent=2))


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
def inspect(
    path: str,
    node: list[str] = typer.Option(None, "--node", "-n", help="Inspect only the selected node ids."),
    runs_dir: str = typer.Option(".agentflow/runs", envvar="AGENTFLOW_RUNS_DIR"),
    output: RunOutputFormat = typer.Option(RunOutputFormat.SUMMARY, "--output", help="Result output format."),
) -> None:
    from agentflow.inspection import build_launch_inspection

    pipeline = _load_pipeline(path)
    try:
        report = build_launch_inspection(pipeline, runs_dir=runs_dir, node_ids=node or None)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--node") from exc
    _echo_inspection(report, output=output)


@app.command()
def run(
    path: str,
    runs_dir: str = typer.Option(".agentflow/runs", envvar="AGENTFLOW_RUNS_DIR"),
    max_concurrent_runs: int = typer.Option(2, envvar="AGENTFLOW_MAX_CONCURRENT_RUNS"),
    output: RunOutputFormat = typer.Option(RunOutputFormat.JSON, "--output", help="Result output format."),
) -> None:
    _run_pipeline_path(path, runs_dir, max_concurrent_runs, output)


@app.command()
def smoke(
    path: str | None = typer.Argument(None, help="Optional pipeline path. Defaults to the bundled real-agent smoke example."),
    runs_dir: str = typer.Option(".agentflow/runs", envvar="AGENTFLOW_RUNS_DIR"),
    max_concurrent_runs: int = typer.Option(2, envvar="AGENTFLOW_MAX_CONCURRENT_RUNS"),
    output: RunOutputFormat = typer.Option(RunOutputFormat.SUMMARY, "--output", help="Result output format."),
    preflight: SmokePreflightMode = typer.Option(
        SmokePreflightMode.AUTO,
        "--preflight",
        help="When to run the bundled local smoke preflight.",
    ),
) -> None:
    selected_path = path or default_smoke_pipeline_path()
    pipeline = _load_pipeline(selected_path)
    if _should_run_smoke_preflight(path, preflight, pipeline=pipeline):
        report = _doctor_report()
        if report.status == "failed":
            _echo_doctor_report(report, output=output)
            raise typer.Exit(code=1)
        if report.status == "warning":
            _echo_doctor_report(report, output=output, err=True)
    _run_pipeline(pipeline, runs_dir, max_concurrent_runs, output)


@app.command()
def doctor(
    output: RunOutputFormat = typer.Option(RunOutputFormat.JSON, "--output", help="Result output format."),
) -> None:
    report = _doctor_report()
    _echo_doctor_report(report, output=output)
    raise typer.Exit(code=0 if report.status != "failed" else 1)


if __name__ == "__main__":
    app()
