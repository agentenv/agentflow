#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
. "$script_dir/custom-local-kimi-helpers.sh"

python_bin="$(agentflow_repo_python "$repo_root")"
expected_anthropic_base_url='https://api.kimi.com/coding/'
tmpdir="$(mktemp -d)"
stdout_path="$tmpdir/claude-live.stdout"
stderr_path="$tmpdir/claude-live.stderr"

cleanup() {
  local exit_code=$?
  trap - EXIT
  if [ "$exit_code" -eq 0 ]; then
    rm -rf "$tmpdir"
    return
  fi

  printf "claude live probe failed in the Kimi-backed bash login shell.\n" >&2

  if [ -f "$stdout_path" ] && [ -s "$stdout_path" ]; then
    printf "\nclaude live probe stdout:\n" >&2
    sed -n '1,80p' "$stdout_path" >&2
  fi

  if [ -f "$stderr_path" ] && [ -s "$stderr_path" ]; then
    local filtered_stderr
    filtered_stderr="$(agentflow_filtered_probe_stderr_contents "$stderr_path")"
    if [ -n "$filtered_stderr" ]; then
      printf "\nclaude live probe stderr:\n" >&2
      printf "%s\n" "$filtered_stderr" >&2
    fi
  fi

  agentflow_report_provider_side_probe_failure "Claude-on-Kimi" "$stdout_path" "$stderr_path"

  printf "\nkept tempdir for debugging: %s\n" "$tmpdir" >&2
}

trap cleanup EXIT

(
  cd "$repo_root"
  agentflow_run_with_timeout "$python_bin" env EXPECTED_ANTHROPIC_BASE_URL="$expected_anthropic_base_url" bash -lic '
set -euo pipefail
command -v kimi >/dev/null 2>&1
unset ANTHROPIC_API_KEY ANTHROPIC_BASE_URL
kimi >/dev/null
[ -n "${ANTHROPIC_API_KEY:-}" ] || {
  echo "kimi did not export ANTHROPIC_API_KEY" >&2
  exit 1
}
[ -n "${ANTHROPIC_BASE_URL:-}" ] || {
  echo "kimi did not export ANTHROPIC_BASE_URL" >&2
  exit 1
}
[ "${ANTHROPIC_BASE_URL%/}" = "${EXPECTED_ANTHROPIC_BASE_URL%/}" ] || {
  printf "Unexpected ANTHROPIC_BASE_URL=%s\n" "$ANTHROPIC_BASE_URL" >&2
  printf "Expected ANTHROPIC_BASE_URL=%s\n" "$EXPECTED_ANTHROPIC_BASE_URL" >&2
  exit 1
}
claude -p "Reply with exactly: claude ok" \
  --output-format text \
  --permission-mode bypassPermissions \
  --tools "" \
  --no-session-persistence
' >"$stdout_path" 2>"$stderr_path"
)

probe_output="$(
  awk 'NF { last = $0 } END { print last }' "$stdout_path"
)"
if [ "$probe_output" != "claude ok" ]; then
  printf "claude live probe returned unexpected output: %s\n" "$probe_output" >&2
  exit 1
fi

printf "claude live probe: ok - %s\n" "$probe_output"
