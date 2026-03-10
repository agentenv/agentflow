#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
. "$script_dir/custom-local-kimi-helpers.sh"

python_bin="$(agentflow_repo_python "$repo_root")"
tmpdir="$(mktemp -d)"
message_path="$tmpdir/codex-live.message"
stdout_path="$tmpdir/codex-live.stdout"
stderr_path="$tmpdir/codex-live.stderr"

cleanup() {
  local exit_code=$?
  trap - EXIT
  if [ "$exit_code" -eq 0 ]; then
    rm -rf "$tmpdir"
    return
  fi

  printf "codex live probe failed in the Kimi-backed bash login shell.\n" >&2

  if [ -f "$message_path" ] && [ -s "$message_path" ]; then
    printf "\ncodex live probe message:\n" >&2
    sed -n '1,80p' "$message_path" >&2
  fi

  if [ -f "$stdout_path" ] && [ -s "$stdout_path" ]; then
    printf "\ncodex live probe stdout:\n" >&2
    sed -n '1,80p' "$stdout_path" >&2
  fi

  if [ -f "$stderr_path" ] && [ -s "$stderr_path" ]; then
    local filtered_stderr
    filtered_stderr="$(agentflow_filtered_probe_stderr_contents "$stderr_path")"
    if [ -n "$filtered_stderr" ]; then
      printf "\ncodex live probe stderr:\n" >&2
      printf "%s\n" "$filtered_stderr" >&2
    fi
  fi

  agentflow_report_provider_side_probe_failure "Codex" "$stdout_path" "$stderr_path"

  printf "\nkept tempdir for debugging: %s\n" "$tmpdir" >&2
}

trap cleanup EXIT

(
  cd "$repo_root"
  agentflow_run_with_timeout "$python_bin" env CODEX_LIVE_MESSAGE_PATH="$message_path" bash -lic '
set -euo pipefail
command -v kimi >/dev/null 2>&1
kimi >/dev/null
unset OPENAI_BASE_URL
codex exec \
  --skip-git-repo-check \
  --sandbox workspace-write \
  -c '\''approval_policy="never"'\'' \
  -c '\''suppress_unstable_features_warning=true'\'' \
  --output-last-message "$CODEX_LIVE_MESSAGE_PATH" \
  "Reply with exactly: codex ok"
' >"$stdout_path" 2>"$stderr_path"
)

probe_output="$(
  tr -d '\r' <"$message_path" | awk 'NF { last = $0 } END { print last }'
)"
if [ "$probe_output" != "codex ok" ]; then
  printf "codex live probe returned unexpected output: %s\n" "$probe_output" >&2
  exit 1
fi

printf "codex live probe: ok - %s\n" "$probe_output"
