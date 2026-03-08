from __future__ import annotations

import subprocess
from pathlib import Path

from agentflow.doctor import build_local_smoke_doctor_report


def test_local_smoke_doctor_report_ok_with_profile_bridge(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="kimi is a function\n", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "ok"
    assert report.as_dict() == {
        "status": "ok",
        "checks": [
            {"name": "codex", "status": "ok", "detail": "Found `codex` at `/tmp/codex`."},
            {"name": "claude", "status": "ok", "detail": "Found `claude` at `/tmp/claude`."},
            {
                "name": "bash_login_startup",
                "status": "ok",
                "detail": "Bash login shells use `~/.profile`, and it references `~/.bashrc`.",
            },
            {
                "name": "kimi_shell_helper",
                "status": "ok",
                "detail": "`kimi` is available in `bash -lic` for the bundled smoke pipeline.",
            },
        ],
    }


def test_local_smoke_doctor_report_fails_when_kimi_helper_missing(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text("export PATH=\"$HOME/bin:$PATH\"\n", encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="bash: type: kimi: not found\n"),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict() == {
        "status": "failed",
        "checks": [
            {"name": "codex", "status": "ok", "detail": "Found `codex` at `/tmp/codex`."},
            {"name": "claude", "status": "ok", "detail": "Found `claude` at `/tmp/claude`."},
            {
                "name": "bash_login_startup",
                "status": "warning",
                "detail": "Bash login shells use `~/.bash_profile`, but it does not reference `~/.bashrc`.",
            },
            {
                "name": "kimi_shell_helper",
                "status": "failed",
                "detail": "`kimi` is unavailable in `bash -lic`; add it to your bash startup files before running the bundled smoke pipeline.",
            },
        ],
    }


def test_local_smoke_doctor_report_checks_kimi_helper_in_supplied_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")

    def fake_run(*args, **kwargs):
        env = kwargs.get("env") or {}
        home_value = env.get("HOME")
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0 if home_value == str(home) else 1,
            stdout="kimi is a function\n" if home_value == str(home) else "",
            stderr="" if home_value == str(home) else "bash: type: kimi: not found\n",
        )

    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "ok"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "ok",
        "detail": "`kimi` is available in `bash -lic` for the bundled smoke pipeline.",
    }
