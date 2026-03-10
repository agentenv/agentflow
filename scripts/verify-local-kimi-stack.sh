#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
. "$script_dir/custom-local-kimi-helpers.sh"
python_bin="$(agentflow_repo_python "$repo_root")"
bundled_smoke_pipeline="$repo_root/examples/local-real-agents-kimi-smoke.yaml"
bundled_shell_init_pipeline="$repo_root/examples/local-real-agents-kimi-shell-init-smoke.yaml"
bundled_shell_wrapper_pipeline="$repo_root/examples/local-real-agents-kimi-shell-wrapper-smoke.yaml"
keep_going="${AGENTFLOW_LOCAL_VERIFY_KEEP_GOING:-}"
step_failures=()
keep_going_tmpdir=""

cleanup() {
  local exit_code=$?
  trap - EXIT
  if [ -n "$keep_going_tmpdir" ] && [ -d "$keep_going_tmpdir" ]; then
    rm -rf "$keep_going_tmpdir"
  fi
  exit "$exit_code"
}

trap cleanup EXIT

if [ "$keep_going" = "1" ]; then
  keep_going_tmpdir="$(mktemp -d)"
fi

record_step_failure() {
  local label="$1"
  local failure_kind="$2"
  local exit_code="$3"

  step_failures+=("$label|$failure_kind|$exit_code")
}

render_failure_summary() {
  local entry=""
  local label=""
  local failure_kind=""
  local exit_code=""
  local saw_provider_side=0

  if [ "${#step_failures[@]}" -eq 0 ]; then
    return 0
  fi

  printf "\n== Verification summary ==\n" >&2
  for entry in "${step_failures[@]}"; do
    IFS='|' read -r label failure_kind exit_code <<EOF
$entry
EOF
    if [ "$failure_kind" = "provider-side" ]; then
      saw_provider_side=1
      printf -- "- %s: provider-side API rejection (exit %s)\n" "$label" "$exit_code" >&2
      continue
    fi
    printf -- "- %s: failed (exit %s)\n" "$label" "$exit_code" >&2
  done

  if [ "$saw_provider_side" -eq 1 ]; then
    printf "Provider-side API rejections mean the local bash + kimi bootstrap likely reached the upstream service; inspect the raw API errors above for account-state or quota issues.\n" >&2
  fi
}

run_step() {
  local label="$1"
  shift

  printf "\n== %s ==\n" "$label"
  if [ "$keep_going" != "1" ]; then
    "$@"
    return 0
  fi

  local stdout_path="$keep_going_tmpdir/$(printf '%s' "$label" | tr ' /()' '____').stdout"
  local stderr_path="$keep_going_tmpdir/$(printf '%s' "$label" | tr ' /()' '____').stderr"
  local exit_code=0
  local failure_kind="failed"

  if "$@" >"$stdout_path" 2>"$stderr_path"; then
    if [ -s "$stdout_path" ]; then
      cat "$stdout_path"
    fi
    if [ -s "$stderr_path" ]; then
      cat "$stderr_path" >&2
    fi
    return 0
  else
    exit_code=$?
  fi

  if [ -s "$stdout_path" ]; then
    cat "$stdout_path"
  fi
  if [ -s "$stderr_path" ]; then
    cat "$stderr_path" >&2
  fi
  if agentflow_probe_failure_is_provider_side "$stdout_path" "$stderr_path"; then
    failure_kind="provider-side"
  fi
  record_step_failure "$label" "$failure_kind" "$exit_code"
  printf 'Continuing after `%s` failure because AGENTFLOW_LOCAL_VERIFY_KEEP_GOING=1.\n' "$label" >&2
}

run_bundled_run_step() {
  local label_suffix="$1"
  local pipeline_path="$2"
  local pipeline_name="$3"
  local expected_trigger="$4"
  local expected_auto_preflight_reason="$5"

  run_step "Bundled run-local${label_suffix}" env \
    AGENTFLOW_BUNDLED_PIPELINE_PATH="$pipeline_path" \
    AGENTFLOW_BUNDLED_PIPELINE_NAME="$pipeline_name" \
    AGENTFLOW_BUNDLED_EXPECTED_TRIGGER="$expected_trigger" \
    AGENTFLOW_BUNDLED_EXPECTED_AUTO_PREFLIGHT_REASON="$expected_auto_preflight_reason" \
    bash "$script_dir/verify-bundled-local-kimi-run.sh"
}

run_bundled_smoke_step() {
  local label_suffix="$1"
  local pipeline_path="$2"
  local pipeline_name="$3"
  local expected_trigger="$4"
  local expected_auto_preflight_reason="$5"

  run_step "Bundled smoke-local${label_suffix}" env \
    AGENTFLOW_BUNDLED_PIPELINE_PATH="$pipeline_path" \
    AGENTFLOW_BUNDLED_PIPELINE_NAME="$pipeline_name" \
    AGENTFLOW_BUNDLED_EXPECTED_TRIGGER="$expected_trigger" \
    AGENTFLOW_BUNDLED_EXPECTED_AUTO_PREFLIGHT_REASON="$expected_auto_preflight_reason" \
    bash "$script_dir/verify-bundled-local-kimi-smoke.sh"
}

run_step "Shell toolchain" bash "$script_dir/verify-local-kimi-shell.sh"
run_step "Bundled toolchain-local" agentflow_run_with_timeout "$python_bin" "$python_bin" -m agentflow toolchain-local --output summary
run_step "Codex live probe" bash "$script_dir/verify-local-kimi-codex-live.sh"
run_step "Claude-on-Kimi live probe" bash "$script_dir/verify-local-kimi-claude-live.sh"
run_step "Bundled inspect-local" agentflow_run_with_timeout "$python_bin" "$python_bin" -m agentflow inspect "$bundled_smoke_pipeline" --output summary
run_step "Bundled doctor-local" agentflow_run_with_timeout "$python_bin" "$python_bin" -m agentflow doctor "$bundled_smoke_pipeline" --output summary
run_bundled_smoke_step "" \
  "$bundled_smoke_pipeline" \
  "local-real-agents-kimi-smoke" \
  "target.bootstrap" \
  "path matches the bundled real-agent smoke pipeline."
run_bundled_run_step "" \
  "$bundled_smoke_pipeline" \
  "local-real-agents-kimi-smoke" \
  "target.bootstrap" \
  "path matches the bundled real-agent smoke pipeline."
run_step "Bundled check-local" agentflow_run_with_timeout "$python_bin" "$python_bin" -m agentflow check-local "$bundled_smoke_pipeline" --output summary
run_step "Bundled inspect-local (shell_init)" agentflow_run_with_timeout "$python_bin" "$python_bin" -m agentflow inspect "$bundled_shell_init_pipeline" --output summary
run_step "Bundled doctor-local (shell_init)" agentflow_run_with_timeout "$python_bin" "$python_bin" -m agentflow doctor "$bundled_shell_init_pipeline" --output summary
run_bundled_smoke_step " (shell_init)" \
  "$bundled_shell_init_pipeline" \
  "local-real-agents-kimi-shell-init-smoke" \
  "target.shell_init" \
  'local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.'
run_bundled_run_step " (shell_init)" \
  "$bundled_shell_init_pipeline" \
  "local-real-agents-kimi-shell-init-smoke" \
  "target.shell_init" \
  'local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.'
run_step "Bundled check-local (shell_init)" agentflow_run_with_timeout "$python_bin" "$python_bin" -m agentflow check-local "$bundled_shell_init_pipeline" --output summary
run_step "Bundled inspect-local (target.shell)" agentflow_run_with_timeout "$python_bin" "$python_bin" -m agentflow inspect "$bundled_shell_wrapper_pipeline" --output summary
run_step "Bundled doctor-local (target.shell)" agentflow_run_with_timeout "$python_bin" "$python_bin" -m agentflow doctor "$bundled_shell_wrapper_pipeline" --output summary
run_bundled_smoke_step " (target.shell)" \
  "$bundled_shell_wrapper_pipeline" \
  "local-real-agents-kimi-shell-wrapper-smoke" \
  "target.shell" \
  'local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.'
run_bundled_run_step " (target.shell)" \
  "$bundled_shell_wrapper_pipeline" \
  "local-real-agents-kimi-shell-wrapper-smoke" \
  "target.shell" \
  'local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.'
run_step "Bundled check-local (target.shell)" agentflow_run_with_timeout "$python_bin" "$python_bin" -m agentflow check-local "$bundled_shell_wrapper_pipeline" --output summary
run_step "External custom doctor" bash "$script_dir/verify-custom-local-kimi-doctor.sh"
run_step "External custom doctor (shell_init)" env AGENTFLOW_KIMI_PIPELINE_MODE=shell-init bash "$script_dir/verify-custom-local-kimi-doctor.sh"
run_step "External custom doctor (target.shell)" env AGENTFLOW_KIMI_PIPELINE_MODE=shell-wrapper bash "$script_dir/verify-custom-local-kimi-doctor.sh"
run_step "External custom inspect" bash "$script_dir/verify-custom-local-kimi-inspect.sh"
run_step "External custom inspect (shell_init)" env AGENTFLOW_KIMI_PIPELINE_MODE=shell-init bash "$script_dir/verify-custom-local-kimi-inspect.sh"
run_step "External custom inspect (target.shell)" env AGENTFLOW_KIMI_PIPELINE_MODE=shell-wrapper bash "$script_dir/verify-custom-local-kimi-inspect.sh"
run_step "External custom smoke" bash "$script_dir/verify-custom-local-kimi-smoke.sh"
run_step "External custom smoke (shell_init)" env AGENTFLOW_KIMI_PIPELINE_MODE=shell-init bash "$script_dir/verify-custom-local-kimi-smoke.sh"
run_step "External custom smoke (target.shell)" env AGENTFLOW_KIMI_PIPELINE_MODE=shell-wrapper bash "$script_dir/verify-custom-local-kimi-smoke.sh"
run_step "External custom check-local" bash "$script_dir/verify-custom-local-kimi-pipeline.sh"
run_step "External custom check-local (shell_init)" bash "$script_dir/verify-custom-local-kimi-shell-init.sh"
run_step "External custom check-local (target.shell)" env AGENTFLOW_KIMI_PIPELINE_MODE=shell-wrapper bash "$script_dir/verify-custom-local-kimi-pipeline.sh"
run_step "External custom run" bash "$script_dir/verify-custom-local-kimi-run.sh"
run_step "External custom run (shell_init)" env AGENTFLOW_KIMI_PIPELINE_MODE=shell-init bash "$script_dir/verify-custom-local-kimi-run.sh"
run_step "External custom run (target.shell)" env AGENTFLOW_KIMI_PIPELINE_MODE=shell-wrapper bash "$script_dir/verify-custom-local-kimi-run.sh"

if [ "${#step_failures[@]}" -gt 0 ]; then
  render_failure_summary
  exit 1
fi
