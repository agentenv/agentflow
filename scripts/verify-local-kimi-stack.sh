#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
python_bin="${AGENTFLOW_PYTHON:-}"

if [ -z "$python_bin" ]; then
  if [ -x "$repo_root/.venv/bin/python" ]; then
    python_bin="$repo_root/.venv/bin/python"
  else
    python_bin="python3"
  fi
fi

run_step() {
  local label="$1"
  shift

  printf "\n== %s ==\n" "$label"
  "$@"
}

run_step "Shell toolchain" bash "$script_dir/verify-local-kimi-shell.sh"
run_step "External custom doctor" bash "$script_dir/verify-custom-local-kimi-doctor.sh"
run_step "External custom inspect" bash "$script_dir/verify-custom-local-kimi-inspect.sh"
run_step "Bundled check-local" "$python_bin" -m agentflow check-local --output summary
run_step "External custom check-local" bash "$script_dir/verify-custom-local-kimi-pipeline.sh"
run_step "External custom run" bash "$script_dir/verify-custom-local-kimi-run.sh"
