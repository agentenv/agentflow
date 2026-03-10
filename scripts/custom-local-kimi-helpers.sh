#!/usr/bin/env bash

_AGENTFLOW_CUSTOM_LOCAL_KIMI_HELPERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_AGENTFLOW_CUSTOM_LOCAL_KIMI_REPO_ROOT="$(cd "$_AGENTFLOW_CUSTOM_LOCAL_KIMI_HELPERS_DIR/.." && pwd)"

agentflow_repo_python() {
  local repo_root="$1"
  local python_bin="${AGENTFLOW_PYTHON:-}"

  if [ -n "$python_bin" ]; then
    printf '%s\n' "$python_bin"
    return
  fi

  if [ -x "$repo_root/.venv/bin/python" ]; then
    printf '%s\n' "$repo_root/.venv/bin/python"
    return
  fi

  printf '%s\n' "python3"
}

agentflow_local_verify_timeout_seconds() {
  local python_bin="$1"
  local timeout_name=""
  local raw_timeout=""

  if [ -n "${AGENTFLOW_LOCAL_VERIFY_TIMEOUT_SECONDS:-}" ]; then
    timeout_name="AGENTFLOW_LOCAL_VERIFY_TIMEOUT_SECONDS"
    raw_timeout="$AGENTFLOW_LOCAL_VERIFY_TIMEOUT_SECONDS"
  elif [ -n "${AGENTFLOW_DOCTOR_TIMEOUT_SECONDS:-}" ]; then
    timeout_name="AGENTFLOW_DOCTOR_TIMEOUT_SECONDS"
    raw_timeout="$AGENTFLOW_DOCTOR_TIMEOUT_SECONDS"
  else
    raw_timeout="60"
  fi

  "$python_bin" - "$raw_timeout" "$timeout_name" <<'PY'
import math
import sys

raw_timeout = sys.argv[1]
timeout_name = sys.argv[2] or "maintainer verify timeout"

try:
    timeout_seconds = float(raw_timeout)
except ValueError as exc:
    raise SystemExit(f"{timeout_name} must be a positive number, got {raw_timeout!r}") from exc

if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
    raise SystemExit(f"{timeout_name} must be a positive number, got {raw_timeout!r}")

print(timeout_seconds)
PY
}

agentflow_run_with_timeout() {
  local python_bin="$1"
  shift

  local timeout_seconds
  timeout_seconds="$(agentflow_local_verify_timeout_seconds "$python_bin")"

  "$python_bin" - "$timeout_seconds" "$@" <<'PY'
import os
import shlex
import subprocess
import sys
import signal

timeout_seconds = float(sys.argv[1])
command = sys.argv[2:]

process = subprocess.Popen(command, start_new_session=True)

try:
    raise SystemExit(process.wait(timeout=timeout_seconds))
except subprocess.TimeoutExpired:
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    print(
        f"Timed out after {timeout_seconds:g}s: {shlex.join(command)}",
        file=sys.stderr,
    )
    raise SystemExit(124)
PY
}

agentflow_filtered_probe_stderr_contents() {
  local stderr_path="$1"

  if [ ! -f "$stderr_path" ]; then
    return 0
  fi

  grep -v \
    -e '^bash: cannot set terminal process group (' \
    -e '^bash: initialize_job_control: no job control in background:' \
    -e '^bash: no job control in this shell$' \
    "$stderr_path" || true
}

agentflow_report_provider_side_probe_failure() {
  local probe_name="$1"
  local stdout_path="$2"
  local stderr_path="$3"
  local stdout_snippet=""
  local stderr_snippet=""
  local combined_output=""

  if [ -f "$stdout_path" ]; then
    stdout_snippet="$(sed -n '1,80p' "$stdout_path")"
  fi
  stderr_snippet="$(agentflow_filtered_probe_stderr_contents "$stderr_path")"
  combined_output="${stdout_snippet}"$'\n'"${stderr_snippet}"

  if ! printf '%s\n' "$combined_output" | grep -Eq 'API Error:'; then
    return 0
  fi

  if printf '%s\n' "$combined_output" | grep -Eqi 'API Error: 402|membership|benefits|billing|credits|quota'; then
    printf "\nDiagnosis: %s reached the provider, but the request was rejected with a membership/billing-style API error. The local bash + kimi bootstrap is likely working; check the upstream provider account state.\n" "$probe_name" >&2
    return 0
  fi

  printf "\nDiagnosis: %s reached the provider, but the request was rejected upstream. The local bash + kimi bootstrap is likely working; inspect the raw API error above.\n" "$probe_name" >&2
}

select_custom_local_kimi_pipeline_mode() {
  local pipeline_mode="${AGENTFLOW_KIMI_PIPELINE_MODE:-bootstrap}"

  case "$pipeline_mode" in
    bootstrap)
      CUSTOM_LOCAL_KIMI_PIPELINE_MODE="$pipeline_mode"
      CUSTOM_LOCAL_KIMI_PIPELINE_SUFFIX=""
      CUSTOM_LOCAL_KIMI_PIPELINE_LABEL="bootstrap"
      CUSTOM_LOCAL_KIMI_EXPECTED_TRIGGER="target.bootstrap"
      CUSTOM_LOCAL_KIMI_PIPELINE_WRITER="write_custom_local_kimi_pipeline"
      ;;
    shell-init)
      CUSTOM_LOCAL_KIMI_PIPELINE_MODE="$pipeline_mode"
      CUSTOM_LOCAL_KIMI_PIPELINE_SUFFIX="-shell-init"
      CUSTOM_LOCAL_KIMI_PIPELINE_LABEL="shell_init"
      CUSTOM_LOCAL_KIMI_EXPECTED_TRIGGER="target.shell_init"
      CUSTOM_LOCAL_KIMI_PIPELINE_WRITER="write_custom_local_kimi_shell_init_pipeline"
      ;;
    shell-wrapper)
      CUSTOM_LOCAL_KIMI_PIPELINE_MODE="$pipeline_mode"
      CUSTOM_LOCAL_KIMI_PIPELINE_SUFFIX="-shell-wrapper"
      CUSTOM_LOCAL_KIMI_PIPELINE_LABEL="target.shell wrapper"
      CUSTOM_LOCAL_KIMI_EXPECTED_TRIGGER="target.shell"
      CUSTOM_LOCAL_KIMI_PIPELINE_WRITER="write_custom_local_kimi_shell_wrapper_pipeline"
      ;;
    *)
      printf 'unsupported AGENTFLOW_KIMI_PIPELINE_MODE: %s\n' "$pipeline_mode" >&2
      return 1
      ;;
  esac
}

write_custom_local_kimi_pipeline_from_example() {
  local pipeline_path="$1"
  local pipeline_name="$2"
  local pipeline_description="$3"
  local example_name="$4"
  local python_bin
  local example_path="$_AGENTFLOW_CUSTOM_LOCAL_KIMI_REPO_ROOT/examples/$example_name"

  python_bin="$(agentflow_repo_python "$_AGENTFLOW_CUSTOM_LOCAL_KIMI_REPO_ROOT")"

  "$python_bin" - "$example_path" "$pipeline_path" "$pipeline_name" "$pipeline_description" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

import yaml


class _LiteralString(str):
    pass


class _LiteralDumper(yaml.SafeDumper):
    pass


def _represent_literal_string(dumper: _LiteralDumper, data: _LiteralString):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|")


def _mark_literal_strings(value):
    if isinstance(value, str):
        return _LiteralString(value) if "\n" in value else value
    if isinstance(value, list):
        return [_mark_literal_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _mark_literal_strings(item) for key, item in value.items()}
    return value


_LiteralDumper.add_representer(_LiteralString, _represent_literal_string)

example_path = Path(sys.argv[1])
pipeline_path = Path(sys.argv[2])
pipeline_name = sys.argv[3]
pipeline_description = sys.argv[4]

if not example_path.is_file():
    fallback_templates = {
        "local-real-agents-kimi-smoke.yaml": """
working_dir: .
concurrency: 2
local_target_defaults:
  bootstrap: kimi
nodes:
  - id: codex_plan
    agent: codex
    env:
      OPENAI_BASE_URL: ""
    prompt: |
      Reply with exactly: codex ok
    timeout_seconds: 180
    success_criteria:
      - kind: output_contains
        value: codex ok
  - id: claude_review
    agent: claude
    provider: kimi
    prompt: |
      Reply with exactly: claude ok
    timeout_seconds: 180
    success_criteria:
      - kind: output_contains
        value: claude ok
""",
        "local-real-agents-kimi-shell-init-smoke.yaml": """
working_dir: .
concurrency: 2
local_target_defaults:
  shell: bash
  shell_login: true
  shell_interactive: true
  shell_init: kimi
nodes:
  - id: codex_plan
    agent: codex
    env:
      OPENAI_BASE_URL: ""
    prompt: |
      Reply with exactly: codex ok
    timeout_seconds: 180
    success_criteria:
      - kind: output_contains
        value: codex ok
  - id: claude_review
    agent: claude
    provider: kimi
    prompt: |
      Reply with exactly: claude ok
    timeout_seconds: 180
    success_criteria:
      - kind: output_contains
        value: claude ok
""",
        "local-real-agents-kimi-shell-wrapper-smoke.yaml": """
working_dir: .
concurrency: 2
local_target_defaults:
  shell: "bash -lic 'command -v kimi >/dev/null 2>&1 && kimi && {command}'"
nodes:
  - id: codex_plan
    agent: codex
    env:
      OPENAI_BASE_URL: ""
    prompt: |
      Reply with exactly: codex ok
    timeout_seconds: 180
    success_criteria:
      - kind: output_contains
        value: codex ok
  - id: claude_review
    agent: claude
    provider: kimi
    prompt: |
      Reply with exactly: claude ok
    timeout_seconds: 180
    success_criteria:
      - kind: output_contains
        value: claude ok
""",
    }
    template_name = example_path.name
    try:
        payload = yaml.safe_load(fallback_templates[template_name])
    except KeyError as exc:
        raise SystemExit(f"Bundled local Kimi example not found: {example_path}") from exc
else:
    payload = yaml.safe_load(example_path.read_text(encoding="utf-8"))
payload["name"] = pipeline_name
payload["description"] = pipeline_description
payload = _mark_literal_strings(payload)

pipeline_path.write_text(
    yaml.dump(payload, Dumper=_LiteralDumper, sort_keys=False),
    encoding="utf-8",
)
PY
}

write_custom_local_kimi_pipeline() {
  local pipeline_path="$1"
  local pipeline_name="$2"
  local pipeline_description="$3"

  write_custom_local_kimi_pipeline_from_example \
    "$pipeline_path" \
    "$pipeline_name" \
    "$pipeline_description" \
    "local-real-agents-kimi-smoke.yaml"
}

write_custom_local_kimi_shell_init_pipeline() {
  local pipeline_path="$1"
  local pipeline_name="$2"
  local pipeline_description="$3"

  write_custom_local_kimi_pipeline_from_example \
    "$pipeline_path" \
    "$pipeline_name" \
    "$pipeline_description" \
    "local-real-agents-kimi-shell-init-smoke.yaml"
}

write_custom_local_kimi_shell_wrapper_pipeline() {
  local pipeline_path="$1"
  local pipeline_name="$2"
  local pipeline_description="$3"

  write_custom_local_kimi_pipeline_from_example \
    "$pipeline_path" \
    "$pipeline_name" \
    "$pipeline_description" \
    "local-real-agents-kimi-shell-wrapper-smoke.yaml"
}
