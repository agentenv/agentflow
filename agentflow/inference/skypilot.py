from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_GPU_SELECTOR_RE = re.compile(
    r"^(?:(?P<cloud>[^:@\s]+):)?(?P<shape>[^@\s]+)(?:@(?P<location>[^@\s]+))?$"
)
_MULTI_NODE_GPU_RE = re.compile(r"^(?P<nodes>\d+)x(?P<count>\d+)x(?P<name>[A-Za-z0-9_.-]+)$")
_GPU_COUNT_RE = re.compile(r"^(?P<count>\d+)x(?P<name>[A-Za-z0-9_.-]+)$")
_SKY_ACCEL_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+):(?P<count>\d+)$")
_GPU_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
BLACKWELL_GPUS = ("B200",)
_DLAMI_SSM_PARAM = (
    "/aws/service/deeplearning/ami/x86_64/"
    "base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
)


@dataclass(frozen=True, slots=True)
class GpuSelector:
    """Normalized AgentFlow shorthand for SkyPilot resources."""

    raw: str
    accelerator: str
    count: int = 1
    cloud: str | None = None
    location: str | None = None
    num_nodes: int | None = None

    @property
    def infra(self) -> str | None:
        if self.cloud is None:
            return None
        if self.location is None:
            return self.cloud
        return f"{self.cloud}/{self.location}"

    @property
    def accelerators(self) -> str:
        return f"{self.accelerator}:{self.count}"


@dataclass(frozen=True, slots=True)
class SkyInferenceRequest:
    model_id: str
    gpu: GpuSelector
    engine: str = "vllm"
    input_path: Path | None = None
    output_path: Path | None = None
    prompt: str | None = None
    batch_size: int = 32
    max_tokens: int = 128
    temperature: float = 0.0
    use_spot: bool = True
    max_hourly_cost: float | None = None
    image_id: str | None = None
    name: str | None = None
    workers: int | None = None
    detach: bool = False
    pool: str | None = None
    wait_interval_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class SkyInferenceLaunch:
    name: str
    job_ids: list[int]
    request_id: str
    detached: bool
    gpu: GpuSelector
    engine: str
    use_spot: bool
    pool: str | None = None
    records: list[dict[str, Any]] | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "job_ids": self.job_ids,
            "request_id": self.request_id,
            "detached": self.detached,
            "gpu": {
                "raw": self.gpu.raw,
                "infra": self.gpu.infra,
                "accelerators": self.gpu.accelerators,
                "num_nodes": self.gpu.num_nodes,
            },
            "engine": self.engine,
            "use_spot": self.use_spot,
            "pool": self.pool,
        }
        if self.records is not None:
            payload["records"] = self.records
        return payload


def parse_gpu_selector(value: str) -> GpuSelector:
    """Parse `aws:8xb200@us-east-1` and `aws:8x8xb200@us-east-2` style selectors."""

    raw = value.strip()
    if not raw:
        raise ValueError("GPU selector must not be empty")

    match = _GPU_SELECTOR_RE.fullmatch(raw)
    if match is None:
        raise ValueError(
            "GPU selector must look like `aws:8xb200@us-east-1`, `aws:8x8xb200@us-east-2`, or `8xh100`"
        )

    cloud = match.group("cloud")
    location = match.group("location")
    shape = match.group("shape")
    num_nodes: int | None = None
    count = 1
    accelerator: str

    multi_node_match = _MULTI_NODE_GPU_RE.fullmatch(shape)
    if multi_node_match is not None:
        num_nodes = _positive_int(multi_node_match.group("nodes"), "node count")
        count = _positive_int(multi_node_match.group("count"), "GPU count")
        accelerator = multi_node_match.group("name")
    else:
        count_match = _GPU_COUNT_RE.fullmatch(shape)
        sky_match = _SKY_ACCEL_RE.fullmatch(shape)
        if count_match is not None:
            count = _positive_int(count_match.group("count"), "GPU count")
            accelerator = count_match.group("name")
        elif sky_match is not None:
            count = _positive_int(sky_match.group("count"), "GPU count")
            accelerator = sky_match.group("name")
        elif "x" not in shape.lower() and _GPU_NAME_RE.fullmatch(shape):
            accelerator = shape
        else:
            raise ValueError(f"Invalid GPU selector shape `{shape}`")

    return GpuSelector(
        raw=raw,
        cloud=_normalize_cloud(cloud),
        location=location,
        accelerator=_normalize_accelerator(accelerator),
        count=count,
        num_nodes=num_nodes,
    )


def build_sky_resources_kwargs(
    selector: GpuSelector,
    *,
    use_spot: bool,
    max_hourly_cost: float | None,
    image_id: str | None = None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "accelerators": selector.accelerators,
        "use_spot": use_spot,
    }
    if selector.infra is not None:
        kwargs["infra"] = selector.infra
    if max_hourly_cost is not None:
        kwargs["max_hourly_cost"] = max_hourly_cost
    resolved_image_id = image_id or resolve_default_image_id(selector)
    if resolved_image_id is not None:
        kwargs["image_id"] = resolved_image_id
    return kwargs


def build_sky_task(request: SkyInferenceRequest, *, sky_module: Any | None = None) -> Any:
    sky = sky_module or _import_sky()
    resources = sky.Resources(**build_sky_resources_kwargs(
        request.gpu,
        use_spot=request.use_spot,
        max_hourly_cost=request.max_hourly_cost,
        image_id=request.image_id,
    ))
    worker_input, file_mounts = _worker_input_path_and_mounts(request.input_path)
    task_kwargs: dict[str, Any] = {
        "name": request.name or default_inference_name(request.model_id),
        "setup": _setup_commands(request.engine),
        "run": _worker_command(request, input_path=worker_input),
        "workdir": str(_repo_root()),
        "resources": resources,
        "envs": _forwarded_envs(),
    }
    if file_mounts:
        task_kwargs["file_mounts"] = file_mounts
    if request.gpu.num_nodes is not None:
        task_kwargs["num_nodes"] = request.gpu.num_nodes
    return sky.Task(**task_kwargs)


def launch_sky_inference_job(request: SkyInferenceRequest, *, sky_module: Any | None = None) -> SkyInferenceLaunch:
    sky = sky_module or _import_sky()
    try:
        from sky import jobs
    except ImportError as exc:  # pragma: no cover - covered by the outer import guard in real installs.
        raise RuntimeError("SkyPilot managed jobs SDK is unavailable in this Python environment.") from exc

    task = build_sky_task(request, sky_module=sky)
    name = request.name or default_inference_name(request.model_id)
    launch_kwargs: dict[str, Any] = {"name": name}
    if request.pool:
        launch_kwargs["pool"] = request.pool
        if request.workers is not None:
            launch_kwargs["num_jobs"] = request.workers

    request_id = jobs.launch(task, **launch_kwargs)
    job_ids, _handle = sky.stream_and_get(request_id)
    normalized_job_ids = [int(job_id) for job_id in (job_ids or [])]
    records = None
    if not request.detach and normalized_job_ids:
        records = wait_for_jobs(
            sky,
            jobs,
            normalized_job_ids,
            interval_seconds=request.wait_interval_seconds,
        )
    return SkyInferenceLaunch(
        name=name,
        job_ids=normalized_job_ids,
        request_id=str(request_id),
        detached=request.detach,
        gpu=request.gpu,
        engine=request.engine,
        use_spot=request.use_spot,
        pool=request.pool,
        records=records,
    )


def wait_for_jobs(sky: Any, jobs: Any, job_ids: list[int], *, interval_seconds: float) -> list[dict[str, Any]]:
    terminal_statuses = {"CANCELLED", "FAILED", "SUCCEEDED"}
    latest_records: list[dict[str, Any]] = []
    while True:
        request_id = jobs.queue_v2(refresh=True, job_ids=job_ids)
        result = sky.get(request_id)
        records = result[0] if isinstance(result, tuple) else result
        latest_records = [_record_to_dict(record) for record in records]
        statuses = {_record_status(record) for record in latest_records}
        if statuses and statuses.issubset(terminal_statuses):
            return latest_records
        time.sleep(interval_seconds)


def default_inference_name(model_id: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", model_id).strip("-").lower()
    if not slug:
        slug = "model"
    return f"agentflow-inference-{slug[:48]}"


def resolve_default_image_id(selector: GpuSelector) -> str | None:
    if selector.cloud != "aws":
        return None
    if selector.accelerator not in BLACKWELL_GPUS:
        return None
    region = _aws_region_from_location(selector.location)
    if region is None:
        return None
    return resolve_blackwell_image(region)


def resolve_blackwell_image(region: str) -> str | None:
    """Resolve the current Blackwell-capable DLAMI for an AWS region."""

    try:
        import boto3

        ssm = boto3.client("ssm", region_name=region)
        return ssm.get_parameter(Name=_DLAMI_SSM_PARAM)["Parameter"]["Value"]
    except Exception as exc:  # noqa: BLE001 - let SkyPilot setup fallback try instead.
        print(
            f"[agentflow] could not resolve a Blackwell DLAMI for {region} ({exc}); "
            "relying on SkyPilot's setup-time driver handling",
            file=sys.stderr,
        )
        return None


def _aws_region_from_location(location: str | None) -> str | None:
    if not location:
        return None
    region = location.split("/", 1)[0]
    if re.fullmatch(r"[a-z]{2}(?:-gov)?-[a-z]+-\d[a-z]", region):
        return region[:-1]
    return region


def _import_sky() -> Any:
    try:
        import sky
    except ImportError as exc:
        raise RuntimeError(
            "SkyPilot is required for `agentflow inference`. Install it with "
            "`pip install 'agentflow[sky]'` or `pip install skypilot`."
        ) from exc
    return sky


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _worker_input_path_and_mounts(input_path: Path | None) -> tuple[str | None, dict[str, str]]:
    if input_path is None:
        return None, {}
    resolved = input_path.expanduser().resolve()
    remote_path = f"/tmp/agentflow-input/{resolved.name}"
    return remote_path, {remote_path: str(resolved)}


def _setup_commands(engine: str) -> list[str]:
    package = "vllm" if engine == "vllm" else "sglang[all]"
    return [
        "python3 -m pip install --upgrade pip",
        "python3 -m pip install -e .",
        f"python3 -m pip install {shlex.quote(package)}",
    ]


def _worker_command(request: SkyInferenceRequest, *, input_path: str | None) -> str:
    command = [
        "python3",
        "-m",
        "agentflow.inference.worker",
        "--engine",
        request.engine,
        "--model-id",
        request.model_id,
        "--output",
        str(request.output_path or "/tmp/agentflow-inference/results.jsonl"),
        "--batch-size",
        str(request.batch_size),
        "--max-tokens",
        str(request.max_tokens),
        "--temperature",
        str(request.temperature),
        "--tensor-parallel-size",
        str(request.gpu.count),
    ]
    if input_path is not None:
        command.extend(["--input", input_path])
    if request.prompt:
        command.extend(["--prompt", request.prompt])
    return " ".join(shlex.quote(part) for part in command)


def _forwarded_envs() -> dict[str, str]:
    envs: dict[str, str] = {}
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = os.getenv(key)
        if value:
            envs[key] = value
    return envs


def _record_to_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return dict(record)
    if hasattr(record, "model_dump"):
        return record.model_dump(mode="json")
    if hasattr(record, "_asdict"):
        return dict(record._asdict())
    return json.loads(json.dumps(record, default=str))


def _record_status(record: dict[str, Any]) -> str:
    status = record.get("status") or record.get("job_status")
    return getattr(status, "value", str(status)).upper()


def _positive_int(value: str, label: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{label} must be positive")
    return parsed


def _normalize_cloud(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _normalize_accelerator(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("accelerator name must not be empty")
    if normalized.lower().startswith("tpu-"):
        return normalized
    return normalized.upper()
