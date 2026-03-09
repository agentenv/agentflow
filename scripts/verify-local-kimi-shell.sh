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

expected_anthropic_base_url='https://api.kimi.com/coding/'
bash_login_startup="$(
  PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" - <<'PY'
from agentflow.local_shell import summarize_target_bash_login_startup

target = {
    "kind": "local",
    "shell": "bash",
    "shell_login": True,
    "shell_interactive": True,
}
print(summarize_target_bash_login_startup(target) or "n/a")
PY
)"

if [ -f "$HOME/.bash_profile" ]; then
  bash_profile_status="present"
else
  bash_profile_status="missing"
fi

if [ -f "$HOME/.bash_login" ]; then
  bash_login_status="present"
else
  bash_login_status="missing"
fi

if [ -f "$HOME/.profile" ]; then
  profile_status="present"
else
  profile_status="missing"
fi

printf "~/.bash_profile: %s\n" "$bash_profile_status"
printf "~/.bash_login: %s\n" "$bash_login_status"
printf "~/.profile: %s\n" "$profile_status"
printf "bash login startup: %s\n" "$bash_login_startup"

EXPECTED_ANTHROPIC_BASE_URL="$expected_anthropic_base_url" bash -lic '
command -v kimi >/dev/null 2>&1
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
printf "ANTHROPIC_BASE_URL=%s\n" "$ANTHROPIC_BASE_URL"
codex_auth_sources=()
if [ -n "${OPENAI_API_KEY:-}" ]; then
  codex_auth_sources+=("OPENAI_API_KEY")
fi
if codex login status >/dev/null 2>&1; then
  codex_auth_sources+=("login")
fi
if [ "${#codex_auth_sources[@]}" -eq 0 ]; then
  echo "codex is not logged in and OPENAI_API_KEY is not exported" >&2
  exit 1
fi
codex_auth_label="${codex_auth_sources[0]}"
for codex_auth_source in "${codex_auth_sources[@]:1}"; do
  codex_auth_label="${codex_auth_label} + ${codex_auth_source}"
done
printf "codex auth: %s\n" "$codex_auth_label"
printf "codex: "
codex --version
printf "claude: "
claude --version
' 2> >(
  grep -v \
    -e '^bash: cannot set terminal process group (' \
    -e '^bash: initialize_job_control: no job control in background:' \
    -e '^bash: no job control in this shell$' >&2
)
