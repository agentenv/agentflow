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
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
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
                "detail": "`kimi` is available in `bash -lic`, exports `ANTHROPIC_API_KEY`, and keeps both `claude` and `codex` available for the bundled smoke pipeline.",
            },
        ],
    }


def test_local_smoke_doctor_report_ok_with_quoted_home_prefix_bridge(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME"/.bashrc ]; then . "$HOME"/.bashrc; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "ok"
    assert report.as_dict()["checks"][2] == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": "Bash login shells use `~/.profile`, and it references `~/.bashrc`.",
    }


def test_local_smoke_doctor_report_follows_transitive_profile_bridge(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text(
        'if [ -f "$HOME/.profile" ]; then . "$HOME/.profile"; fi\n',
        encoding="utf-8",
    )
    (home / ".profile").write_text(
        'if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n',
        encoding="utf-8",
    )
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "ok"
    assert report.as_dict()["checks"][2] == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": "Bash login shells use `~/.bash_profile`, and it reaches `~/.bashrc` via `~/.profile`.",
    }


def test_local_smoke_doctor_report_follows_transitive_quoted_home_bridge(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text(
        'if [ -f "${HOME}"/.profile ]; then . "${HOME}"/.profile; fi\n',
        encoding="utf-8",
    )
    (home / ".profile").write_text(
        'if [ -f "${HOME}"/.bashrc ]; then . "${HOME}"/.bashrc; fi\n',
        encoding="utf-8",
    )
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "ok"
    assert report.as_dict()["checks"][2] == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": "Bash login shells use `~/.bash_profile`, and it reaches `~/.bashrc` via `~/.profile`.",
    }


def test_local_smoke_doctor_report_accepts_runtime_ready_shell_when_referenced_bashrc_is_missing(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.as_dict()["checks"][2] == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": (
            "Bash login shells use `~/.profile`, and `bash -lic` already exposes `kimi`, `claude`, and `codex`; "
            "a `~/.bashrc` bridge is not required for the bundled smoke pipeline."
        ),
    }


def test_local_smoke_doctor_report_accepts_runtime_ready_shell_without_bash_login_file(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.as_dict()["checks"][2] == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": (
            "No `~/.bash_profile`, `~/.bash_login`, or `~/.profile` was found, but `bash -lic` already exposes "
            "`kimi`, `claude`, and `codex`; a `~/.bashrc` bridge is not required for the bundled smoke pipeline."
        ),
    }


def test_local_smoke_doctor_report_accepts_runtime_ready_shell_when_bash_profile_shadows_profile_bridge(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text('export PATH="$HOME/bin:$PATH"\n', encoding="utf-8")
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.as_dict()["checks"][2] == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": (
            "Bash login shells use `~/.bash_profile`, and `bash -lic` already exposes `kimi`, `claude`, and "
            "`codex`; a `~/.bashrc` bridge is not required for the bundled smoke pipeline."
        ),
    }


def test_local_smoke_doctor_report_accepts_runtime_ready_shell_with_commented_bashrc_reference(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text(
        '# if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n',
        encoding="utf-8",
    )
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.as_dict()["checks"][2] == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": (
            "Bash login shells use `~/.profile`, and `bash -lic` already exposes `kimi`, `claude`, and `codex`; "
            "a `~/.bashrc` bridge is not required for the bundled smoke pipeline."
        ),
    }


def test_local_smoke_doctor_report_accepts_runtime_ready_shell_with_commented_transitive_bridge(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text(
        '# if [ -f "$HOME/.profile" ]; then . "$HOME/.profile"; fi\n',
        encoding="utf-8",
    )
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.as_dict()["checks"][2] == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": (
            "Bash login shells use `~/.bash_profile`, and `bash -lic` already exposes `kimi`, `claude`, and "
            "`codex`; a `~/.bashrc` bridge is not required for the bundled smoke pipeline."
        ),
    }


def test_local_smoke_doctor_report_fails_when_kimi_helper_missing(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text("export PATH=\"$HOME/bin:$PATH\"\n", encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=11, stdout="", stderr="bash: type: kimi: not found\n"),
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
            returncode=0 if home_value == str(home) else 11,
            stdout="",
            stderr="" if home_value == str(home) else "bash: type: kimi: not found\n",
        )

    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "ok"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "ok",
        "detail": "`kimi` is available in `bash -lic`, exports `ANTHROPIC_API_KEY`, and keeps both `claude` and `codex` available for the bundled smoke pipeline.",
    }


def test_local_smoke_doctor_report_warns_when_claude_is_only_available_in_bash_shell(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    def fake_which(name: str) -> str | None:
        if name == "codex":
            return "/tmp/codex"
        return None

    monkeypatch.setattr("agentflow.doctor.shutil.which", fake_which)
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "warning"
    assert report.as_dict()["checks"][1] == {
        "name": "claude",
        "status": "warning",
        "detail": "`claude` is not on PATH outside the smoke shell bootstrap; `bash -lic` plus `kimi` must provide it for the bundled smoke pipeline.",
    }
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "ok",
        "detail": "`kimi` is available in `bash -lic`, exports `ANTHROPIC_API_KEY`, and keeps both `claude` and `codex` available for the bundled smoke pipeline.",
    }


def test_local_smoke_doctor_report_warns_when_codex_is_only_available_in_bash_shell(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    def fake_which(name: str) -> str | None:
        if name == "claude":
            return "/tmp/claude"
        return None

    monkeypatch.setattr("agentflow.doctor.shutil.which", fake_which)
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "warning"
    assert report.as_dict()["checks"][0] == {
        "name": "codex",
        "status": "warning",
        "detail": "`codex` is not on PATH outside the bundled smoke login shell; `bash -lic` must provide it for the local smoke pipeline.",
    }


def test_local_smoke_doctor_report_fails_when_kimi_helper_does_not_export_api_key(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=13, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "failed",
        "detail": "`kimi` runs in `bash -lic`, but it does not export `ANTHROPIC_API_KEY`; the bundled smoke pipeline will not be able to authenticate Claude-on-Kimi.",
    }


def test_local_smoke_doctor_report_fails_when_claude_is_missing_in_bash_shell(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=12, stdout="", stderr="bash: type: claude: not found\n"),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "failed",
        "detail": "`kimi` runs in `bash -lic`, but `claude` is unavailable afterwards; the bundled smoke pipeline will not be able to launch Claude-on-Kimi.",
    }


def test_local_smoke_doctor_report_fails_when_codex_is_missing_after_kimi_bootstrap(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=14, stdout="", stderr="bash: type: codex: not found\n"),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "failed",
        "detail": "`kimi` runs in `bash -lic`, but `codex` is unavailable afterwards; the bundled smoke pipeline will not be able to launch Codex inside that shared Kimi bootstrap.",
    }


def test_local_smoke_doctor_report_fails_when_bash_cannot_launch(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("bash not found")

    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict() == {
        "status": "failed",
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
                "status": "failed",
                "detail": "Could not launch `bash -lic` to verify `kimi`, `claude`, and `codex`: bash not found",
            },
        ],
    }
