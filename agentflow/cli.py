from __future__ import annotations

import asyncio
import json

import typer
from agentflow.defaults import default_smoke_pipeline_path
from agentflow.doctor import build_local_smoke_doctor_report

app = typer.Typer(add_completion=False)


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


def _run_pipeline_path(path: str, runs_dir: str, max_concurrent_runs: int) -> None:
    _, orchestrator = _build_runtime(runs_dir, max_concurrent_runs)
    pipeline = _load_pipeline(path)

    async def _run() -> None:
        run_record = await orchestrator.submit(pipeline)
        completed = await orchestrator.wait(run_record.id, timeout=None)
        typer.echo(json.dumps(completed.model_dump(mode="json"), indent=2))
        raise typer.Exit(code=0 if completed.status.value == "completed" else 1)

    asyncio.run(_run())


def _doctor_report():
    return build_local_smoke_doctor_report()


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
def run(
    path: str,
    runs_dir: str = typer.Option(".agentflow/runs", envvar="AGENTFLOW_RUNS_DIR"),
    max_concurrent_runs: int = typer.Option(2, envvar="AGENTFLOW_MAX_CONCURRENT_RUNS"),
) -> None:
    _run_pipeline_path(path, runs_dir, max_concurrent_runs)


@app.command()
def smoke(
    path: str | None = typer.Argument(None, help="Optional pipeline path. Defaults to the bundled real-agent smoke example."),
    runs_dir: str = typer.Option(".agentflow/runs", envvar="AGENTFLOW_RUNS_DIR"),
    max_concurrent_runs: int = typer.Option(2, envvar="AGENTFLOW_MAX_CONCURRENT_RUNS"),
) -> None:
    if path is None:
        report = _doctor_report()
        if report.status == "failed":
            typer.echo(json.dumps(report.as_dict(), indent=2))
            raise typer.Exit(code=1)
    _run_pipeline_path(path or default_smoke_pipeline_path(), runs_dir, max_concurrent_runs)


@app.command()
def doctor() -> None:
    report = _doctor_report()
    typer.echo(json.dumps(report.as_dict(), indent=2))
    raise typer.Exit(code=0 if report.status != "failed" else 1)


if __name__ == "__main__":
    app()
