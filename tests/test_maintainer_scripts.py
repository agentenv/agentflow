from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}", encoding="utf-8")
    path.chmod(0o755)


def _repo_python(repo_root: Path) -> str:
    python_bin = repo_root / ".venv" / "bin" / "python"
    return str(python_bin if python_bin.exists() else Path(sys.executable))


def _run_script(script_path: Path, *, repo_root: Path, home: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        cwd=repo_root,
        env={
            **os.environ,
            "AGENTFLOW_PYTHON": _repo_python(repo_root),
            "HOME": str(home),
            **env,
        },
        text=True,
        timeout=5,
    )


def _write_fake_shell_home(home: Path, *, kimi_body: str, startup_file: str = ".profile") -> None:
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True)
    (home / startup_file).write_text(
        'if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n',
        encoding="utf-8",
    )
    (home / ".bashrc").write_text(
        'export PATH="$HOME/bin:$PATH"\n'
        "kimi() {\n"
        f"{textwrap.indent(kimi_body.rstrip(), '  ')}\n"
        "}\n",
        encoding="utf-8",
    )
    _write_executable(
        bin_dir / "codex",
        'if [ "${1:-}" = "login" ] && [ "${2:-}" = "status" ]; then\n'
        "  exit 0\n"
        "fi\n"
        'printf "codex-cli 0.0.0\\n"\n',
    )
    _write_executable(bin_dir / "claude", 'printf "Claude Code 0.0.0\\n"\n')


def test_verify_local_kimi_shell_script_reports_bash_profile_startup_when_present(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_fake_shell_home(
        home,
        startup_file=".bash_profile",
        kimi_body=(
            "export ANTHROPIC_BASE_URL=https://api.kimi.com/coding/\n"
            "export ANTHROPIC_API_KEY=test-kimi-key\n"
        ),
    )

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "verify-local-kimi-shell.sh"

    completed = _run_script(script_path, repo_root=repo_root, home=home, OPENAI_API_KEY="")

    assert completed.returncode == 0
    assert "~/.bash_profile: present" in completed.stdout
    assert "~/.bash_login: missing" in completed.stdout
    assert "~/.profile: missing" in completed.stdout
    assert "bash login startup: ~/.bash_profile -> ~/.bashrc" in completed.stdout
    assert "bash login bridge: not needed" in completed.stdout
    assert "ANTHROPIC_BASE_URL=https://api.kimi.com/coding/" in completed.stdout
    assert "codex auth: login" in completed.stdout
    assert "codex: codex-cli 0.0.0" in completed.stdout
    assert "claude: Claude Code 0.0.0" in completed.stdout
    assert completed.stderr == ""


def test_verify_local_kimi_shell_script_times_out_when_kimi_hangs(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_fake_shell_home(home, kimi_body="sleep 5\n")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "verify-local-kimi-shell.sh"

    started_at = time.monotonic()
    completed = _run_script(
        script_path,
        repo_root=repo_root,
        home=home,
        AGENTFLOW_LOCAL_VERIFY_TIMEOUT_SECONDS="0.2",
    )
    elapsed = time.monotonic() - started_at

    assert completed.returncode == 124
    assert "~/.profile: present" in completed.stdout
    assert "Timed out after 0.2s: env" in completed.stderr
    assert elapsed < 3
