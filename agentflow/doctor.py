from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


_BASH_LOGIN_FILENAMES = (".bash_profile", ".bash_login", ".profile")
_KIMI_HELPER_MISSING_EXIT_CODE = 11
_CLAUDE_IN_SHELL_MISSING_EXIT_CODE = 12
_KIMI_API_KEY_MISSING_EXIT_CODE = 13


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    status: str
    checks: list[DoctorCheck]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "checks": [asdict(check) for check in self.checks],
        }


def _check_executable(name: str) -> DoctorCheck:
    path = shutil.which(name)
    if path:
        return DoctorCheck(name=name, status="ok", detail=f"Found `{name}` at `{path}`.")
    return DoctorCheck(name=name, status="failed", detail=f"`{name}` is not on PATH.")


def _check_claude_host_executable() -> DoctorCheck:
    path = shutil.which("claude")
    if path:
        return DoctorCheck(name="claude", status="ok", detail=f"Found `claude` at `{path}`.")
    return DoctorCheck(
        name="claude",
        status="warning",
        detail=(
            "`claude` is not on PATH outside the smoke shell bootstrap; "
            "`bash -lic` plus `kimi` must provide it for the bundled smoke pipeline."
        ),
    )


def _bash_login_file(home: Path) -> Path | None:
    for filename in _BASH_LOGIN_FILENAMES:
        candidate = home / filename
        if candidate.exists():
            return candidate
    return None


def _bash_startup_chain_to_bashrc(
    home: Path,
    startup_file: Path,
    seen: frozenset[str] = frozenset(),
) -> tuple[str, ...] | None:
    name = startup_file.name
    if name in seen:
        return None

    text = startup_file.read_text(encoding="utf-8", errors="ignore")
    if ".bashrc" in text:
        return (name, ".bashrc")

    next_seen = seen | {name}
    for filename in _BASH_LOGIN_FILENAMES:
        if filename == name or filename in next_seen:
            continue
        candidate = home / filename
        if not candidate.exists() or filename not in text:
            continue
        chain = _bash_startup_chain_to_bashrc(home, candidate, next_seen)
        if chain is not None:
            return (name, *chain)
    return None


def _format_bash_startup_paths(paths: tuple[str, ...]) -> str:
    formatted = [f"`~/{path}`" for path in paths]
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]} and {formatted[1]}"
    return f"{', '.join(formatted[:-1])}, and {formatted[-1]}"


def _check_bash_login_startup(home: Path) -> DoctorCheck:
    login_file = _bash_login_file(home)
    if login_file is None:
        return DoctorCheck(
            name="bash_login_startup",
            status="warning",
            detail=(
                "No `~/.bash_profile`, `~/.bash_login`, or `~/.profile` was found for bash login shells; "
                "create one that sources `~/.bashrc` if you expect login shells to load your `kimi` helper."
            ),
        )

    chain = _bash_startup_chain_to_bashrc(home, login_file)
    if chain is None:
        return DoctorCheck(
            name="bash_login_startup",
            status="warning",
            detail=f"Bash login shells use `~/{login_file.name}`, but it does not reference `~/.bashrc`.",
        )

    bashrc_path = home / ".bashrc"
    if not bashrc_path.exists():
        if len(chain) == 2:
            detail = f"Bash login shells use `~/{login_file.name}`, and it references `~/.bashrc`, but `~/.bashrc` does not exist."
        else:
            detail = (
                f"Bash login shells use `~/{login_file.name}`, and it reaches `~/.bashrc` "
                f"via {_format_bash_startup_paths(chain[1:-1])}, but `~/.bashrc` does not exist."
            )
        return DoctorCheck(name="bash_login_startup", status="warning", detail=detail)

    if len(chain) == 2:
        return DoctorCheck(
            name="bash_login_startup",
            status="ok",
            detail=f"Bash login shells use `~/{login_file.name}`, and it references `~/.bashrc`.",
        )

    return DoctorCheck(
        name="bash_login_startup",
        status="ok",
        detail=(
            f"Bash login shells use `~/{login_file.name}`, and it reaches `~/.bashrc` "
            f"via {_format_bash_startup_paths(chain[1:-1])}."
        ),
    )


def _check_kimi_shell_helper(home: Path | None = None) -> DoctorCheck:
    env = os.environ.copy()
    if home is not None:
        env["HOME"] = str(home)
    script = "\n".join(
        [
            f"type {shlex.quote('kimi')} >/dev/null 2>&1 || exit {_KIMI_HELPER_MISSING_EXIT_CODE}",
            "kimi >/dev/null || exit $?",
            f'[ -n "${{ANTHROPIC_API_KEY:-}}" ] || exit {_KIMI_API_KEY_MISSING_EXIT_CODE}',
            f"type {shlex.quote('claude')} >/dev/null 2>&1 || exit {_CLAUDE_IN_SHELL_MISSING_EXIT_CODE}",
        ]
    )
    try:
        result = subprocess.run(
            ["bash", "-lic", script],
            check=False,
            capture_output=True,
            env=env,
            text=True,
        )
    except OSError as exc:
        return DoctorCheck(
            name="kimi_shell_helper",
            status="failed",
            detail=f"Could not launch `bash -lic` to verify `kimi` and `claude`: {exc}",
        )
    if result.returncode == 0:
        return DoctorCheck(
            name="kimi_shell_helper",
            status="ok",
            detail=(
                "`kimi` is available in `bash -lic`, exports `ANTHROPIC_API_KEY`, "
                "and keeps `claude` available for the bundled smoke pipeline."
            ),
        )
    if result.returncode == _KIMI_HELPER_MISSING_EXIT_CODE:
        return DoctorCheck(
            name="kimi_shell_helper",
            status="failed",
            detail="`kimi` is unavailable in `bash -lic`; add it to your bash startup files before running the bundled smoke pipeline.",
        )
    if result.returncode == _CLAUDE_IN_SHELL_MISSING_EXIT_CODE:
        return DoctorCheck(
            name="kimi_shell_helper",
            status="failed",
            detail=(
                "`kimi` runs in `bash -lic`, but `claude` is unavailable afterwards; "
                "the bundled smoke pipeline will not be able to launch Claude-on-Kimi."
            ),
        )
    if result.returncode == _KIMI_API_KEY_MISSING_EXIT_CODE:
        return DoctorCheck(
            name="kimi_shell_helper",
            status="failed",
            detail=(
                "`kimi` runs in `bash -lic`, but it does not export `ANTHROPIC_API_KEY`; "
                "the bundled smoke pipeline will not be able to authenticate Claude-on-Kimi."
            ),
        )
    detail = result.stderr.strip() or f"exit status {result.returncode}"
    return DoctorCheck(
        name="kimi_shell_helper",
        status="failed",
        detail=f"`kimi` failed inside `bash -lic`: {detail}",
    )


def build_local_smoke_doctor_report(home: Path | None = None) -> DoctorReport:
    resolved_home = home or Path.home()
    checks = [
        _check_executable("codex"),
        _check_claude_host_executable(),
        _check_bash_login_startup(resolved_home),
        _check_kimi_shell_helper(resolved_home),
    ]
    if any(check.status == "failed" for check in checks):
        status = "failed"
    elif any(check.status == "warning" for check in checks):
        status = "warning"
    else:
        status = "ok"
    return DoctorReport(status=status, checks=checks)
