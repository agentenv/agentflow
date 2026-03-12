from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace

from agentflow.doctor import (
    DoctorCheck,
    _CODEX_AUTH_VIA_API_KEY_AND_LOGIN_STATUS_EXIT_CODE,
    _CODEX_AUTH_VIA_API_KEY_EXIT_CODE,
    _CODEX_AUTH_VIA_LOGIN_STATUS_EXIT_CODE,
    _check_claude_executable,
    _check_bash_login_startup,
    _check_kimi_shell_helper,
    _prepared_kimi_readiness_execution,
    _should_probe_local_claude,
    build_bash_login_shell_bridge_recommendation,
    build_local_kimi_bootstrap_doctor_report,
    build_local_smoke_doctor_report,
    build_pipeline_local_claude_readiness_checks,
    build_pipeline_local_codex_auth_checks,
    build_pipeline_local_codex_auth_info_checks,
    build_pipeline_local_codex_readiness_checks,
    build_pipeline_local_codex_readiness_info_checks,
    build_pipeline_local_kimi_readiness_checks,
    build_pipeline_local_kimi_readiness_info_checks,
)
from agentflow.prepared import ExecutionPaths
from agentflow.specs import ProviderConfig, provider_uses_kimi_anthropic_auth


_KIMI_HELPER_OK_DETAIL = (
    "`kimi` is available in `bash -lic`, exports `ANTHROPIC_API_KEY`, "
    "sets `ANTHROPIC_BASE_URL=https://api.kimi.com/coding/`, keeps both `claude` and `codex` available, "
    "and confirms Codex authentication is ready via `codex login status` or `OPENAI_API_KEY` for the bundled smoke pipeline."
)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}", encoding="utf-8")
    path.chmod(0o755)


def _write_local_kimi_shell_home(home: Path, *, kimi_body: str = ":") -> None:
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True)
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
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


def _startup_context(
    *startup_chain: str,
    login_file: str | None = None,
    shadowed_startup_chain: tuple[str, ...] | None = None,
    bashrc_exists: bool | None = None,
    runtime_ready: bool | None = None,
) -> dict[str, object]:
    startup_files = {
        "~/.bash_profile": "missing",
        "~/.bash_login": "missing",
        "~/.profile": "missing",
    }
    if login_file in startup_files:
        startup_files[login_file] = "present"
    for path in startup_chain:
        if path in startup_files:
            startup_files[path] = "present"
    if shadowed_startup_chain is not None:
        for path in shadowed_startup_chain:
            if path in startup_files:
                startup_files[path] = "present"

    context: dict[str, object] = {
        "login_file": login_file,
        "startup_chain": list(startup_chain),
        "startup_summary": "none" if not startup_chain else " -> ".join(startup_chain),
        "startup_files": startup_files,
        "startup_files_summary": ", ".join(f"{path}={status}" for path, status in startup_files.items()),
        "bashrc_reachable": bool(startup_chain and startup_chain[-1] == "~/.bashrc"),
    }
    if shadowed_startup_chain is not None:
        context["shadowed_startup_chain"] = list(shadowed_startup_chain)
        context["shadowed_startup_summary"] = " -> ".join(shadowed_startup_chain)
    if bashrc_exists is not None:
        context["bashrc_exists"] = bashrc_exists
    if runtime_ready is not None:
        context["runtime_ready"] = runtime_ready
    return context


def test_should_probe_local_claude_for_case_mixed_kimi_provider():
    node = SimpleNamespace(
        agent=SimpleNamespace(value="claude"),
        provider=ProviderConfig(
            name="Kimi",
            base_url="https://api.kimi.com/coding/",
            api_key_env="ANTHROPIC_API_KEY",
        ),
        target=SimpleNamespace(kind="local", shell="bash", shell_login=True, shell_interactive=True, shell_init=None),
    )

    assert _should_probe_local_claude(node) is True


def test_should_probe_local_claude_for_custom_kimi_provider_base_url():
    node = SimpleNamespace(
        agent=SimpleNamespace(value="claude"),
        provider=ProviderConfig(
            name="kimi-proxy",
            base_url="https://api.kimi.com/coding/",
            api_key_env="ANTHROPIC_API_KEY",
        ),
        target=SimpleNamespace(kind="local", shell="bash", shell_login=True, shell_interactive=True, shell_init=None),
    )

    assert _should_probe_local_claude(node) is True


def test_should_probe_local_claude_for_custom_kimi_provider_env_base_url():
    node = SimpleNamespace(
        agent=SimpleNamespace(value="claude"),
        provider=ProviderConfig(
            name="kimi-proxy",
            api_key_env="ANTHROPIC_API_KEY",
            env={"ANTHROPIC_BASE_URL": "https://api.kimi.com/coding/"},
        ),
        target=SimpleNamespace(kind="local", shell="bash", shell_login=True, shell_interactive=True, shell_init=None),
    )

    assert _should_probe_local_claude(node) is True


def test_provider_uses_kimi_anthropic_auth_for_custom_api_key_env():
    provider = ProviderConfig(
        name="kimi-proxy",
        base_url="https://api.kimi.com/coding/",
        api_key_env="KIMI_PROXY_KEY",
    )

    assert provider_uses_kimi_anthropic_auth(provider) is True


def test_should_probe_local_claude_for_generic_local_target():
    node = SimpleNamespace(
        agent=SimpleNamespace(value="claude"),
        provider=None,
        target=SimpleNamespace(kind="local", shell=None, shell_login=False, shell_interactive=False, shell_init=None),
    )

    assert _should_probe_local_claude(node) is True


def test_should_probe_local_claude_when_login_shell_exposes_kimi_on_path(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('export PATH="$HOME/bin:$PATH"\n', encoding="utf-8")
    bin_dir = home / "bin"
    bin_dir.mkdir()
    kimi = bin_dir / "kimi"
    kimi.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    kimi.chmod(0o755)
    node = SimpleNamespace(
        agent=SimpleNamespace(value="claude"),
        provider=ProviderConfig(
            name="kimi",
            base_url="https://api.kimi.com/coding/",
            api_key_env="ANTHROPIC_API_KEY",
        ),
        target=SimpleNamespace(
            kind="local",
            shell=f"env HOME={home} bash",
            shell_login=True,
            shell_interactive=False,
            shell_init="kimi",
        ),
    )

    assert _should_probe_local_claude(node) is True


def test_pipeline_local_codex_checks_use_custom_executable(monkeypatch):
    pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="codex_plan",
                agent=SimpleNamespace(value="codex"),
                provider=None,
                env={},
                executable="custom-codex",
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    captured_target_commands: list[str] = []

    def fake_run(*args, **kwargs):
        env = kwargs.get("env") or {}
        captured_target_commands.append(env.get("AGENTFLOW_TARGET_COMMAND", ""))
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    assert build_pipeline_local_codex_readiness_checks(pipeline) == []
    assert build_pipeline_local_codex_auth_checks(pipeline) == []
    assert build_pipeline_local_codex_readiness_info_checks(pipeline) == [
        DoctorCheck(
            name="codex_ready",
            status="ok",
            detail=(
                "Node `codex_plan` (codex) can launch local Codex after the node shell bootstrap; "
                "`custom-codex --version` succeeds in the prepared local shell."
            ),
        )
    ]
    assert build_pipeline_local_codex_auth_info_checks(pipeline) == [
        DoctorCheck(
            name="codex_auth",
            status="ok",
            detail=(
                "Node `codex_plan` (codex) can authenticate local Codex after the node shell bootstrap via "
                "`codex login status` or `OPENAI_API_KEY`."
            ),
        )
    ]
    assert "custom-codex --version" in captured_target_commands
    assert any(
        "custom-codex" in command and "OPENAI_API_KEY" in command and "subprocess.run" in command
        for command in captured_target_commands
    )


def test_pipeline_local_codex_auth_checks_use_provider_api_key_env(monkeypatch):
    pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="codex_plan",
                agent=SimpleNamespace(value="codex"),
                provider=ProviderConfig(
                    name="openrouter",
                    base_url="https://openrouter.ai/api/v1",
                    api_key_env="OPENROUTER_API_KEY",
                    env={"OPENROUTER_API_KEY": "provider-openrouter-key"},
                ),
                env={},
                executable="custom-codex",
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    captured_target_commands: list[str] = []
    captured_launch_envs: list[dict[str, str]] = []

    def fake_run(*args, **kwargs):
        env = kwargs.get("env") or {}
        captured_launch_envs.append(env)
        captured_target_commands.append(env.get("AGENTFLOW_TARGET_COMMAND", ""))
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    assert build_pipeline_local_codex_auth_checks(pipeline) == []
    assert build_pipeline_local_codex_auth_info_checks(pipeline) == [
        DoctorCheck(
            name="codex_auth",
            status="ok",
            detail=(
                "Node `codex_plan` (codex) can authenticate local Codex after the node shell bootstrap via "
                "`OPENROUTER_API_KEY`."
            ),
        )
    ]
    assert any(env.get("OPENROUTER_API_KEY") == "provider-openrouter-key" for env in captured_launch_envs)
    assert any("OPENROUTER_API_KEY" in command and "subprocess.run" not in command for command in captured_target_commands)
    assert all("OPENAI_API_KEY" not in command for command in captured_target_commands)


def test_pipeline_local_codex_auth_info_checks_report_openai_api_key_source(monkeypatch):
    pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="codex_plan",
                agent=SimpleNamespace(value="codex"),
                provider=None,
                env={},
                executable="custom-codex",
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )

    def fake_run(*args, **kwargs):
        env = kwargs.get("env") or {}
        target_command = env.get("AGENTFLOW_TARGET_COMMAND", "")
        if "custom-codex --version" in target_command:
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")
        if "custom-codex" in target_command and "OPENAI_API_KEY" in target_command and "subprocess.run" in target_command:
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=_CODEX_AUTH_VIA_API_KEY_EXIT_CODE,
                stdout="",
                stderr="",
            )
        raise AssertionError(f"unexpected target command: {target_command}")

    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai-key")
    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    assert build_pipeline_local_codex_auth_info_checks(pipeline) == [
        DoctorCheck(
            name="codex_auth",
            status="ok",
            detail=(
                "Node `codex_plan` (codex) can authenticate local Codex after the node shell bootstrap via "
                "`OPENAI_API_KEY`."
            ),
        )
    ]


def test_pipeline_local_codex_auth_info_checks_report_combined_auth_sources(monkeypatch):
    pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="codex_plan",
                agent=SimpleNamespace(value="codex"),
                provider=None,
                env={},
                executable="custom-codex",
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )

    def fake_run(*args, **kwargs):
        env = kwargs.get("env") or {}
        target_command = env.get("AGENTFLOW_TARGET_COMMAND", "")
        if "custom-codex --version" in target_command:
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")
        if "custom-codex" in target_command and "OPENAI_API_KEY" in target_command and "subprocess.run" in target_command:
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=_CODEX_AUTH_VIA_API_KEY_AND_LOGIN_STATUS_EXIT_CODE,
                stdout="",
                stderr="",
            )
        raise AssertionError(f"unexpected target command: {target_command}")

    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai-key")
    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    assert build_pipeline_local_codex_auth_info_checks(pipeline) == [
        DoctorCheck(
            name="codex_auth",
            status="ok",
            detail=(
                "Node `codex_plan` (codex) can authenticate local Codex after the node shell bootstrap via "
                "`OPENAI_API_KEY` + `codex login status`."
            ),
        )
    ]


def test_pipeline_local_codex_auth_info_checks_report_login_status_source(monkeypatch):
    pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="codex_plan",
                agent=SimpleNamespace(value="codex"),
                provider=None,
                env={},
                executable="custom-codex",
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )

    def fake_run(*args, **kwargs):
        env = kwargs.get("env") or {}
        target_command = env.get("AGENTFLOW_TARGET_COMMAND", "")
        if "custom-codex --version" in target_command:
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")
        if "custom-codex" in target_command and "OPENAI_API_KEY" in target_command and "subprocess.run" in target_command:
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=_CODEX_AUTH_VIA_LOGIN_STATUS_EXIT_CODE,
                stdout="",
                stderr="",
            )
        raise AssertionError(f"unexpected target command: {target_command}")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    assert build_pipeline_local_codex_auth_info_checks(pipeline) == [
        DoctorCheck(
            name="codex_auth",
            status="ok",
            detail=(
                "Node `codex_plan` (codex) can authenticate local Codex after the node shell bootstrap via "
                "`codex login status`."
            ),
        )
    ]


def test_pipeline_local_codex_auth_check_reports_missing_provider_api_key_env(monkeypatch):
    pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="codex_plan",
                agent=SimpleNamespace(value="codex"),
                provider=ProviderConfig(
                    name="openrouter",
                    base_url="https://openrouter.ai/api/v1",
                    api_key_env="OPENROUTER_API_KEY",
                ),
                env={},
                executable="custom-codex",
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    captured_target_commands: list[str] = []

    def fake_run(*args, **kwargs):
        env = kwargs.get("env") or {}
        target_command = env.get("AGENTFLOW_TARGET_COMMAND", "")
        captured_target_commands.append(target_command)
        if "custom-codex --version" in target_command:
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")
        if "OPENROUTER_API_KEY" in target_command:
            return subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="")
        raise AssertionError(f"unexpected target command: {target_command}")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    assert build_pipeline_local_codex_auth_checks(pipeline) == [
        DoctorCheck(
            name="codex_auth",
            status="failed",
            detail=(
                "Node `codex_plan` (codex) cannot authenticate local Codex after the node shell bootstrap; "
                "`OPENROUTER_API_KEY` is not set in the current environment, `node.env`, or `provider.env`."
            ),
        )
    ]
    assert any("OPENROUTER_API_KEY" in command and "subprocess.run" not in command for command in captured_target_commands)
    assert all("OPENAI_API_KEY" not in command for command in captured_target_commands)


def test_prepared_kimi_readiness_execution_prefers_repo_venv_python(monkeypatch, tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_python = repo_root / ".venv" / "bin" / "python"
    repo_python.parent.mkdir(parents=True)
    repo_python.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    paths = ExecutionPaths(
        host_workdir=tmp_path,
        host_runtime_dir=tmp_path / ".runtime",
        target_workdir=str(tmp_path),
        target_runtime_dir=str(tmp_path / ".runtime"),
        app_root=repo_root,
    )
    node = SimpleNamespace(
        id="kimi_review",
        agent=SimpleNamespace(value="kimi"),
        provider=None,
        env={},
        executable=None,
        target=SimpleNamespace(kind="local", shell=None, shell_login=False, shell_interactive=False, shell_init=None, cwd=None),
    )

    monkeypatch.setattr("agentflow.doctor.build_execution_paths", lambda **kwargs: paths)

    prepared, prepared_paths, probe_command = _prepared_kimi_readiness_execution(node)

    assert prepared_paths == paths
    assert prepared.command == ["kimi", "--version"]
    assert probe_command == "kimi --version"


def test_kimi_readiness_info_reports_repo_venv_python(monkeypatch, tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_python = repo_root / ".venv" / "bin" / "python"
    repo_python.parent.mkdir(parents=True)
    repo_python.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    paths = ExecutionPaths(
        host_workdir=tmp_path,
        host_runtime_dir=tmp_path / ".runtime",
        target_workdir=str(tmp_path),
        target_runtime_dir=str(tmp_path / ".runtime"),
        app_root=repo_root,
    )
    pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="kimi_review",
                agent=SimpleNamespace(value="kimi"),
                provider=None,
                env={"KIMI_API_KEY": "inline-secret"},
                executable=None,
                target=SimpleNamespace(kind="local", shell=None, shell_login=False, shell_interactive=False, shell_init=None, cwd=None),
            )
        ],
        working_path=tmp_path,
    )

    monkeypatch.setattr("agentflow.doctor.build_execution_paths", lambda **kwargs: paths)
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    assert [check.as_dict() for check in build_pipeline_local_kimi_readiness_info_checks(pipeline)] == [
        {
            "name": "kimi_ready",
            "status": "ok",
            "detail": (
                "Node `kimi_review` (kimi) found the Kimi CLI after the node shell bootstrap; "
                "`kimi --version` succeeds in the prepared local shell."
            ),
        }
    ]


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
                "detail": "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` exists, and it references `~/.bashrc`.",
                "context": _startup_context(
                    "~/.profile",
                    "~/.bashrc",
                    login_file="~/.profile",
                    bashrc_exists=True,
                ),
            },
            {
                "name": "kimi_shell_helper",
                "status": "ok",
                "detail": _KIMI_HELPER_OK_DETAIL,
            },
        ],
    }


def test_local_kimi_bootstrap_doctor_report_ok_with_profile_bridge(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    report = build_local_kimi_bootstrap_doctor_report(home=home)

    assert report.status == "ok"
    assert report.as_dict() == {
        "status": "ok",
        "checks": [
            {
                "name": "bash_login_startup",
                "status": "ok",
                "detail": "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` exists, and it references `~/.bashrc`.",
                "context": _startup_context(
                    "~/.profile",
                    "~/.bashrc",
                    login_file="~/.profile",
                    bashrc_exists=True,
                ),
            },
            {
                "name": "kimi_shell_helper",
                "status": "ok",
                "detail": (
                    "`kimi` is available in `bash -lic`, exports `ANTHROPIC_API_KEY`, and sets "
                    "`ANTHROPIC_BASE_URL=https://api.kimi.com/coding/`."
                ),
            },
        ],
    }


def test_local_kimi_bootstrap_doctor_report_accepts_runtime_ready_shell_without_login_file(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    report = build_local_kimi_bootstrap_doctor_report(home=home)

    assert report.status == "ok"
    assert report.as_dict()["checks"][0] == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": (
            "No `~/.bash_profile`, `~/.bash_login`, or `~/.profile` was found, but `bash -lic` already exposes "
            "`kimi`; a `~/.bashrc` bridge is not required for this local Kimi bootstrap."
        ),
        "context": _startup_context(runtime_ready=True),
    }


def test_local_kimi_bootstrap_doctor_report_requires_kimi_to_export_anthropic_env(
    tmp_path: Path,
    monkeypatch,
):
    home = tmp_path / "home"
    home.mkdir()
    _write_local_kimi_shell_home(home)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-kimi-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.kimi.com/coding/")

    report = build_local_kimi_bootstrap_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "failed",
        "detail": "`kimi` runs in `bash -lic`, but it does not export `ANTHROPIC_API_KEY`.",
    }


def test_local_smoke_doctor_report_includes_host_cli_versions(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")

    def fake_run(*args, **kwargs):
        command = args[0]
        if command == ["/tmp/codex", "--version"]:
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="codex-cli 0.111.0\n", stderr="")
        if command == ["/tmp/claude", "--version"]:
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="2.1.52 (Claude Code)\n", stderr="")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "ok"
    assert report.as_dict()["checks"][0] == {
        "name": "codex",
        "status": "ok",
        "detail": "Found `codex` at `/tmp/codex` (version `codex-cli 0.111.0`).",
        "context": {"path": "/tmp/codex", "version": "codex-cli 0.111.0"},
    }
    assert report.as_dict()["checks"][1] == {
        "name": "claude",
        "status": "ok",
        "detail": "Found `claude` at `/tmp/claude` (version `2.1.52 (Claude Code)`).",
        "context": {"path": "/tmp/claude", "version": "2.1.52 (Claude Code)"},
    }


def test_check_claude_executable_warns_when_version_probe_times_out(monkeypatch):
    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: "/tmp/claude")

    def fake_run(*args, **kwargs):
        command = args[0]
        if command == ["/tmp/claude", "--version"]:
            raise subprocess.TimeoutExpired(cmd=command, timeout=15)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    check = _check_claude_executable()

    assert check.as_dict() == {
        "name": "claude",
        "status": "warning",
        "detail": "Found `claude` at `/tmp/claude`, but `claude --version` timed out after 15s.",
        "context": {"path": "/tmp/claude", "version_timeout_seconds": 15.0},
    }


def test_check_kimi_shell_helper_fails_when_probe_times_out(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=15)

    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    check = _check_kimi_shell_helper()

    assert check.as_dict() == {
        "name": "kimi_shell_helper",
        "status": "failed",
        "detail": "`kimi` verification in `bash -lic` did not finish: `bash -lic '<inline shell probe>'` timed out after 15s.",
    }


def test_check_kimi_shell_helper_requires_kimi_to_export_anthropic_env(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    _write_local_kimi_shell_home(home)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-kimi-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.kimi.com/coding/")

    check = _check_kimi_shell_helper(home=home)

    assert check.as_dict() == {
        "name": "kimi_shell_helper",
        "status": "failed",
        "detail": (
            "`kimi` runs in `bash -lic`, but it does not export `ANTHROPIC_API_KEY`; "
            "the bundled smoke pipeline will not be able to authenticate Claude-on-Kimi."
        ),
    }


def test_pipeline_local_claude_readiness_check_reports_timeout(monkeypatch):
    pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="claude_review",
                agent=SimpleNamespace(value="claude"),
                provider=None,
                env={},
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=15)

    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    checks = build_pipeline_local_claude_readiness_checks(pipeline)

    assert [check.as_dict() for check in checks] == [
        {
                "name": "claude_ready",
                "status": "failed",
                "detail": (
                    "Node `claude_review` (claude) cannot finish the local preflight probe after the node shell bootstrap; "
                    "`bash -l -i -c 'kimi && eval \"$AGENTFLOW_TARGET_COMMAND\"'` timed out after 15s "
                    "in the prepared local shell."
                ),
            }
        ]


def test_pipeline_local_codex_auth_check_reports_timeout(monkeypatch):
    pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="codex_plan",
                agent=SimpleNamespace(value="codex"),
                provider=None,
                env={},
                executable="custom-codex",
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    commands: list[list[str]] = []

    def fake_run(*args, **kwargs):
        command = list(args[0])
        commands.append(command)
        target_command = (kwargs.get("env") or {}).get("AGENTFLOW_TARGET_COMMAND", "")
        if "custom-codex --version" in target_command:
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")
        if "custom-codex" in target_command and "OPENAI_API_KEY" in target_command and "subprocess.run" in target_command:
            raise subprocess.TimeoutExpired(cmd=command, timeout=15)
        raise AssertionError(f"unexpected target command: {target_command}")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    checks = build_pipeline_local_codex_auth_checks(pipeline)

    assert [check.as_dict() for check in checks] == [
        {
                "name": "codex_auth",
                "status": "failed",
                "detail": (
                    "Node `codex_plan` (codex) cannot finish the local preflight probe after the node shell bootstrap; "
                    "`bash -l -i -c 'kimi && eval \"$AGENTFLOW_TARGET_COMMAND\"'` timed out after 15s "
                    "in the prepared local shell."
                ),
            }
    ]
    assert len(commands) == 2


def test_pipeline_local_codex_auth_check_does_not_trust_ambient_key_when_shell_clears_it(monkeypatch):
    pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="codex_plan",
                agent=SimpleNamespace(value="codex"),
                provider=None,
                env={},
                executable="custom-codex",
                target=SimpleNamespace(
                    kind="local",
                    shell="env OPENAI_API_KEY= bash -c",
                    shell_login=False,
                    shell_interactive=False,
                    shell_init=None,
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )
    commands: list[str] = []

    def fake_run(*args, **kwargs):
        command = list(args[0])
        target_command = (kwargs.get("env") or {}).get("AGENTFLOW_TARGET_COMMAND", "")
        if not target_command and command:
            target_command = command[-1]
        commands.append(target_command)
        if "custom-codex --version" in target_command:
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")
        if "custom-codex" in target_command and "OPENAI_API_KEY" in target_command and "subprocess.run" in target_command:
            return subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="")
        raise AssertionError(f"unexpected target command: {target_command}")

    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai-key")
    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    checks = build_pipeline_local_codex_auth_checks(pipeline)

    assert [check.as_dict() for check in checks] == [
        {
            "name": "codex_auth",
            "status": "failed",
            "detail": (
                "Node `codex_plan` (codex) cannot authenticate local Codex after the node shell bootstrap; "
                "`codex login status` fails and `OPENAI_API_KEY` is not set in the current environment, `node.env`, "
                "or `provider.env`."
            ),
        }
    ]
    assert len(commands) == 2


def test_pipeline_local_kimi_readiness_check_reports_timeout(monkeypatch):
    pipeline = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id="kimi_review",
                agent=SimpleNamespace(value="kimi"),
                provider=None,
                env={},
                executable="python-kimi",
                target=SimpleNamespace(
                    kind="local",
                    shell="bash",
                    shell_login=True,
                    shell_interactive=True,
                    shell_init="kimi",
                    cwd=None,
                ),
            )
        ],
        working_path=Path.cwd(),
    )

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=15)

    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    checks = build_pipeline_local_kimi_readiness_checks(pipeline)

    assert [check.as_dict() for check in checks] == [
        {
                "name": "kimi_ready",
                "status": "failed",
                "detail": (
                    "Node `kimi_review` (kimi) cannot finish the local preflight probe after the node shell bootstrap; "
                    "`bash -l -i -c 'kimi && eval \"$AGENTFLOW_TARGET_COMMAND\"'` timed out after 15s "
                    "in the prepared local shell."
                ),
            }
        ]


def test_check_claude_executable_warns_when_login_shell_provides_claude(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: None)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    check = _check_claude_executable(home)

    assert check.as_dict() == {
        "name": "claude",
        "status": "warning",
        "detail": "`claude` is not on PATH outside the bundled smoke login shell; `bash -lic` must provide it for the local smoke pipeline.",
    }


def test_check_claude_executable_fails_when_login_shell_cannot_find_claude(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: None)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=12, stdout="", stderr="")

    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    check = _check_claude_executable(home)

    assert check.as_dict() == {
        "name": "claude",
        "status": "failed",
        "detail": "`claude` is not on PATH and is unavailable in `bash -lic`.",
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
        "detail": "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` exists, and it references `~/.bashrc`.",
        "context": _startup_context("~/.profile", "~/.bashrc", login_file="~/.profile", bashrc_exists=True),
    }


def test_local_smoke_doctor_report_ok_with_absolute_home_bridge(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text(f'if [ -f "{home}/.bashrc" ]; then . "{home}/.bashrc"; fi\n', encoding="utf-8")
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
        "detail": "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` exists, and it references `~/.bashrc`.",
        "context": _startup_context("~/.profile", "~/.bashrc", login_file="~/.profile", bashrc_exists=True),
    }


def test_local_smoke_doctor_report_warns_when_relative_bashrc_bridge_uses_current_cwd_outside_home(
    tmp_path: Path,
):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('. .bashrc\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    startup_check = _check_bash_login_startup(home)

    assert startup_check.as_dict() == {
        "name": "bash_login_startup",
        "status": "warning",
        "detail": (
            "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` "
            "exists, but it does not reference `~/.bashrc`."
        ),
        "context": _startup_context("~/.profile", login_file="~/.profile"),
    }
    assert build_bash_login_shell_bridge_recommendation(home) is not None


def test_local_smoke_doctor_report_accepts_relative_bashrc_bridge_from_home_cwd(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('. .bashrc\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    startup_check = _check_bash_login_startup(home, cwd=home)

    assert startup_check.as_dict() == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` exists, and it references `~/.bashrc`.",
        "context": _startup_context("~/.profile", "~/.bashrc", login_file="~/.profile", bashrc_exists=True),
    }
    assert build_bash_login_shell_bridge_recommendation(home, cwd=home) is None


def test_local_smoke_doctor_report_accepts_direct_profile_base_url_bootstrap(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text(
        "export ANTHROPIC_BASE_URL=https://api.kimi.com/coding/\n",
        encoding="utf-8",
    )

    startup_check = _check_bash_login_startup(home)

    assert startup_check.as_dict() == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": (
            "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` "
            "exists, and it provides AgentFlow bootstrap directly without `~/.bashrc`."
        ),
        "context": _startup_context("~/.profile", login_file="~/.profile"),
    }
    assert build_bash_login_shell_bridge_recommendation(home) is not None


def test_local_smoke_doctor_report_accepts_profile_env_bridge_exports(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    auth_file = tmp_path / "anthropic.env"
    auth_file.write_text("export ANTHROPIC_API_KEY=from-env-bridge\n", encoding="utf-8")
    (home / ".profile").write_text(
        'if [ -n "${AGENTFLOW_KIMI_ENV_FILE:-}" ]; then . "$AGENTFLOW_KIMI_ENV_FILE"; fi\n',
        encoding="utf-8",
    )

    startup_check = _check_bash_login_startup(
        home,
        env={"AGENTFLOW_KIMI_ENV_FILE": str(auth_file)},
    )

    assert startup_check.as_dict() == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": (
            "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` "
            "exists, and it provides AgentFlow bootstrap directly without `~/.bashrc`."
        ),
        "context": _startup_context("~/.profile", login_file="~/.profile"),
    }
    assert build_bash_login_shell_bridge_recommendation(home) is not None


def test_local_smoke_doctor_report_accepts_bash_login_bridge(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_login").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    startup_check = _check_bash_login_startup(home)

    assert startup_check.as_dict() == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": "Bash login shells use `~/.bash_login`, and it references `~/.bashrc`.",
        "context": _startup_context("~/.bash_login", "~/.bashrc", login_file="~/.bash_login", bashrc_exists=True),
    }
    assert build_bash_login_shell_bridge_recommendation(home) is None


def test_local_smoke_doctor_report_accepts_symlinked_home_bashrc(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    dotfiles = tmp_path / "dotfiles"
    home.mkdir()
    dotfiles.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (dotfiles / "bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")
    (home / ".bashrc").symlink_to(dotfiles / "bashrc")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=11, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.as_dict()["checks"][2] == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` exists, and it references `~/.bashrc`.",
        "context": _startup_context("~/.profile", "~/.bashrc", login_file="~/.profile", bashrc_exists=True),
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
        "context": _startup_context("~/.bash_profile", "~/.profile", "~/.bashrc", login_file="~/.bash_profile", bashrc_exists=True),
    }


def test_local_smoke_doctor_report_warns_when_bash_login_shadows_profile_bridge(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_login").write_text('export PATH="$HOME/bin:$PATH"\n', encoding="utf-8")
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    startup_check = _check_bash_login_startup(home)

    assert startup_check.as_dict() == {
        "name": "bash_login_startup",
        "status": "warning",
        "detail": (
            "Bash login shells use `~/.bash_login`, so `~/.profile` will never run even though it references "
            "`~/.bashrc`; reference `~/.bashrc` or `~/.profile` from `~/.bash_login`."
        ),
        "context": _startup_context(
            "~/.bash_login",
            login_file="~/.bash_login",
            shadowed_startup_chain=("~/.profile", "~/.bashrc"),
        ),
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
        "context": _startup_context("~/.bash_profile", "~/.profile", "~/.bashrc", login_file="~/.bash_profile", bashrc_exists=True),
    }


def test_local_smoke_doctor_report_follows_transitive_absolute_home_bridge(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text(
        f'if [ -f "{home}/.profile" ]; then . "{home}/.profile"; fi\n',
        encoding="utf-8",
    )
    (home / ".profile").write_text(
        f'if [ -f "{home}/.bashrc" ]; then . "{home}/.bashrc"; fi\n',
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
        "context": _startup_context("~/.bash_profile", "~/.profile", "~/.bashrc", login_file="~/.bash_profile", bashrc_exists=True),
    }


def test_local_smoke_doctor_report_keeps_custom_home_bridge_when_kimi_fails(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text(
        'if [ -f "$HOME/.bash_agentflow" ]; then . "$HOME/.bash_agentflow"; fi\n',
        encoding="utf-8",
    )
    (home / ".bash_agentflow").write_text(
        'if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n',
        encoding="utf-8",
    )
    (home / ".bashrc").write_text("# bridge present\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=11, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict()["checks"][2] == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": "Bash login shells use `~/.bash_profile`, and it reaches `~/.bashrc` via `~/.bash_agentflow`.",
        "context": _startup_context(
            "~/.bash_profile",
            "~/.bash_agentflow",
            "~/.bashrc",
            login_file="~/.bash_profile",
            bashrc_exists=True,
        ),
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
            "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` exists, and `bash -lic` already exposes `kimi`, `claude`, and `codex`; "
            "a `~/.bashrc` bridge is not required for the bundled smoke pipeline."
        ),
        "context": _startup_context(
            "~/.profile",
            "~/.bashrc",
            login_file="~/.profile",
            bashrc_exists=False,
            runtime_ready=True,
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
        "context": _startup_context(login_file=None, runtime_ready=True),
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
        "context": _startup_context(
            "~/.bash_profile",
            login_file="~/.bash_profile",
            shadowed_startup_chain=("~/.profile", "~/.bashrc"),
            runtime_ready=True,
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
            "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` exists, and `bash -lic` already exposes `kimi`, `claude`, and `codex`; "
            "a `~/.bashrc` bridge is not required for the bundled smoke pipeline."
        ),
        "context": _startup_context("~/.profile", login_file="~/.profile", runtime_ready=True),
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
        "context": _startup_context(
            "~/.bash_profile",
            login_file="~/.bash_profile",
            shadowed_startup_chain=("~/.profile", "~/.bashrc"),
            runtime_ready=True,
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
                "context": _startup_context("~/.bash_profile", login_file="~/.bash_profile"),
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
        "detail": _KIMI_HELPER_OK_DETAIL,
    }


def test_local_smoke_doctor_report_accepts_claude_when_bootstrap_already_provides_it(tmp_path: Path, monkeypatch):
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

    assert report.status == "ok"
    assert report.as_dict()["checks"][1] == {
        "name": "claude",
        "status": "ok",
        "detail": "`claude` is not on PATH outside the smoke shell bootstrap, but `bash -lic` plus `kimi` already provides it for the bundled smoke pipeline.",
    }
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "ok",
        "detail": _KIMI_HELPER_OK_DETAIL,
    }


def test_local_smoke_doctor_report_accepts_codex_when_bootstrap_already_provides_it(tmp_path: Path, monkeypatch):
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

    assert report.status == "ok"
    assert report.as_dict()["checks"][0] == {
        "name": "codex",
        "status": "ok",
        "detail": "`codex` is not on PATH outside the smoke shell bootstrap, but `bash -lic` plus `kimi` already provides it for the bundled smoke pipeline.",
    }


def test_local_smoke_doctor_report_accepts_codex_when_kimi_bootstrap_provides_it(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    def fake_which(name: str) -> str | None:
        if name == "claude":
            return "/tmp/claude"
        return None

    def fake_run(*args, **kwargs):
        script = args[0][-1]
        if script.startswith("type codex"):
            return subprocess.CompletedProcess(args=args[0], returncode=10, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agentflow.doctor.shutil.which", fake_which)
    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "ok"
    assert report.as_dict()["checks"][0] == {
        "name": "codex",
        "status": "ok",
        "detail": "`codex` is not on PATH outside the smoke shell bootstrap, but `bash -lic` plus `kimi` already provides it for the bundled smoke pipeline.",
    }
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "ok",
        "detail": _KIMI_HELPER_OK_DETAIL,
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


def test_local_smoke_doctor_report_fails_when_kimi_helper_does_not_export_base_url(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=15, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "failed",
        "detail": "`kimi` runs in `bash -lic`, but it does not export `ANTHROPIC_BASE_URL`; the bundled smoke pipeline will not be able to route Claude through Kimi.",
    }


def test_local_smoke_doctor_report_fails_when_kimi_helper_exports_wrong_base_url(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=16,
            stdout="https://open.bigmodel.cn/api/anthropic\n",
            stderr="",
        ),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "failed",
        "detail": "`kimi` runs in `bash -lic`, but `ANTHROPIC_BASE_URL` is `https://open.bigmodel.cn/api/anthropic` instead of `https://api.kimi.com/coding/`; the bundled smoke pipeline will not be able to route Claude through Kimi.",
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


def test_local_smoke_doctor_report_fails_when_claude_cannot_launch_after_kimi_bootstrap(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=18, stdout="", stderr="claude failed\n"),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "failed",
        "detail": "`kimi` runs in `bash -lic`, and `claude` is on PATH afterwards, but `claude --version` still fails; the bundled smoke pipeline will not be able to launch Claude-on-Kimi.",
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


def test_local_smoke_doctor_report_fails_when_codex_cannot_launch_after_kimi_bootstrap(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=19, stdout="", stderr="codex failed\n"),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "failed",
        "detail": "`kimi` runs in `bash -lic`, and `codex` is on PATH afterwards, but `codex --version` still fails; the bundled smoke pipeline will not be able to launch Codex inside that shared Kimi bootstrap.",
    }


def test_local_smoke_doctor_report_fails_when_codex_is_not_logged_in_after_kimi_bootstrap(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=17, stdout="", stderr="Not logged in\n"),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "failed",
        "detail": "`kimi` runs in `bash -lic`, and `codex` is on PATH afterwards, but neither `codex login status` succeeds nor `OPENAI_API_KEY` is exported; make sure Codex is logged in or `OPENAI_API_KEY` is exported in that shared smoke shell.",
    }


def test_local_smoke_doctor_report_accepts_openai_api_key_when_codex_login_status_is_unavailable(
    tmp_path: Path,
    monkeypatch,
):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_API_KEY", "super-secret")
    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")

    def fake_run(*args, **kwargs):
        command = args[0]
        if command[:2] == ["bash", "-lic"]:
            assert "codex --version >/dev/null 2>&1" in command[2]
            assert 'if [ -n "${OPENAI_API_KEY:-}" ]; then ' in command[2]
            assert (
                f"codex login status >/dev/null 2>&1 && exit "
                f"{_CODEX_AUTH_VIA_API_KEY_AND_LOGIN_STATUS_EXIT_CODE}; "
                f"exit {_CODEX_AUTH_VIA_API_KEY_EXIT_CODE}; fi"
            ) in command[2]
            assert f"codex login status >/dev/null 2>&1 && exit {_CODEX_AUTH_VIA_LOGIN_STATUS_EXIT_CODE}" in command[2]
        return subprocess.CompletedProcess(args=command, returncode=_CODEX_AUTH_VIA_API_KEY_EXIT_CODE, stdout="", stderr="")

    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "ok"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "ok",
        "detail": (
            "`kimi` is available in `bash -lic`, exports `ANTHROPIC_API_KEY`, "
            "sets `ANTHROPIC_BASE_URL=https://api.kimi.com/coding/`, keeps both `claude` and `codex` available, "
            "and confirms Codex authentication is ready via `OPENAI_API_KEY` for the bundled smoke pipeline."
        ),
    }


def test_local_smoke_doctor_report_accepts_combined_codex_auth_sources(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_API_KEY", "super-secret")
    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=_CODEX_AUTH_VIA_API_KEY_AND_LOGIN_STATUS_EXIT_CODE,
            stdout="",
            stderr="",
        ),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "ok"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "ok",
        "detail": (
            "`kimi` is available in `bash -lic`, exports `ANTHROPIC_API_KEY`, "
            "sets `ANTHROPIC_BASE_URL=https://api.kimi.com/coding/`, keeps both `claude` and `codex` available, "
            "and confirms Codex authentication is ready via `OPENAI_API_KEY` + `codex login status` for the bundled smoke pipeline."
        ),
    }


def test_local_smoke_doctor_report_accepts_codex_login_status_when_openai_api_key_is_unavailable(
    tmp_path: Path,
    monkeypatch,
):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=_CODEX_AUTH_VIA_LOGIN_STATUS_EXIT_CODE,
            stdout="",
            stderr="",
        ),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "ok"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "ok",
        "detail": (
            "`kimi` is available in `bash -lic`, exports `ANTHROPIC_API_KEY`, "
            "sets `ANTHROPIC_BASE_URL=https://api.kimi.com/coding/`, keeps both `claude` and `codex` available, "
            "and confirms Codex authentication is ready via `codex login status` for the bundled smoke pipeline."
        ),
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
                "detail": "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` exists, and it references `~/.bashrc`.",
                "context": _startup_context(
                    "~/.profile",
                    "~/.bashrc",
                    login_file="~/.profile",
                    bashrc_exists=True,
                ),
            },
            {
                "name": "kimi_shell_helper",
                "status": "failed",
                "detail": "Could not launch `bash -lic` to verify `kimi`, `claude`, and `codex`: bash not found",
            },
        ],
    }


def test_local_smoke_doctor_report_redacts_sensitive_unknown_kimi_stderr(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout="",
            stderr=(
                "bash: cannot set terminal process group (1234): Inappropriate ioctl for device\n"
                "bash: initialize_job_control: no job control in background: Bad file descriptor\n"
                "bash: no job control in this shell\n"
                "export ANTHROPIC_API_KEY=super-secret\n"
                "Authorization: Bearer top-secret\n"
                "kimi bootstrap failed\n"
            ),
        ),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict()["checks"][-1] == {
        "name": "kimi_shell_helper",
        "status": "failed",
        "detail": "`kimi` failed inside `bash -lic`: export ANTHROPIC_API_KEY=<redacted>\nAuthorization: <redacted>\nkimi bootstrap failed",
    }


def test_local_smoke_doctor_report_redacts_sensitive_unknown_codex_stderr(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    def fake_which(name: str):
        if name == "claude":
            return "/tmp/claude"
        return None

    def fake_run(*args, **kwargs):
        command = args[0]
        if command[:2] == ["bash", "-lic"] and "type codex" in command[2]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=1,
                stdout="",
                stderr='OPENAI_API_KEY="super-secret"\n',
            )
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agentflow.doctor.shutil.which", fake_which)
    monkeypatch.setattr("agentflow.doctor.subprocess.run", fake_run)

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict()["checks"][0] == {
        "name": "codex",
        "status": "failed",
        "detail": "`codex` is not on PATH, and `bash -lic` failed while looking for it: OPENAI_API_KEY=<redacted>",
    }


def test_local_smoke_doctor_report_warns_when_login_file_is_unreadable(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    login_file = home / ".bash_profile"
    login_file.write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    os.chmod(login_file, 0)

    monkeypatch.setattr("agentflow.doctor.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(
        "agentflow.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=11, stdout="", stderr=""),
    )

    report = build_local_smoke_doctor_report(home=home)

    assert report.status == "failed"
    assert report.as_dict()["checks"][2] == {
        "name": "bash_login_startup",
        "status": "warning",
        "detail": (
            "Bash login shells use `~/.bash_profile`, but AgentFlow could not read `~/.bash_profile` while "
            "checking whether login shells reach `~/.bashrc`: Permission denied."
        ),
        "context": _startup_context("~/.bash_profile", login_file="~/.bash_profile"),
    }


def test_shell_bridge_recommendation_targets_profile_when_no_login_file_exists(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()

    recommendation = build_bash_login_shell_bridge_recommendation(home=home)

    assert recommendation is not None
    assert recommendation.as_dict() == {
        "target": "~/.profile",
        "source": "~/.bashrc",
        "snippet": 'if [ -f "$HOME/.bashrc" ]; then\n  . "$HOME/.bashrc"\nfi\n',
        "reason": (
            "No `~/.bash_profile`, `~/.bash_login`, or `~/.profile` was found for bash login shells, "
            "so create a minimal startup file that reaches `~/.bashrc`."
        ),
    }


def test_shell_bridge_recommendation_targets_active_login_file_when_bridge_is_missing(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text('export PATH="$HOME/bin:$PATH"\n', encoding="utf-8")

    recommendation = build_bash_login_shell_bridge_recommendation(home=home)

    assert recommendation is not None
    assert recommendation.as_dict() == {
        "target": "~/.bash_profile",
        "source": "~/.bashrc",
        "snippet": 'if [ -f "$HOME/.bashrc" ]; then\n  . "$HOME/.bashrc"\nfi\n',
        "reason": "Bash login shells use `~/.bash_profile`, but it does not reference `~/.bashrc`.",
    }


def test_bash_login_startup_ignores_echoed_source_text(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('echo source ~/.bashrc\n', encoding="utf-8")

    startup_check = _check_bash_login_startup(home)

    assert startup_check.as_dict() == {
        "name": "bash_login_startup",
        "status": "warning",
        "detail": (
            "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` "
            "exists, but it does not reference `~/.bashrc`."
        ),
        "context": _startup_context("~/.profile", login_file="~/.profile"),
    }


def test_bash_login_startup_accepts_relative_bashrc_bridge(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f .bashrc ]; then . .bashrc; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    startup_check = _check_bash_login_startup(home, cwd=home)

    assert startup_check.as_dict() == {
        "name": "bash_login_startup",
        "status": "ok",
        "detail": (
            "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` "
            "exists, and it references `~/.bashrc`."
        ),
        "context": _startup_context(
            "~/.profile",
            "~/.bashrc",
            login_file="~/.profile",
            bashrc_exists=True,
        ),
    }


def test_shell_bridge_recommendation_ignores_echoed_source_text(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('echo source ~/.bashrc\n', encoding="utf-8")

    recommendation = build_bash_login_shell_bridge_recommendation(home=home)

    assert recommendation is not None
    assert recommendation.as_dict() == {
        "target": "~/.profile",
        "source": "~/.bashrc",
        "snippet": 'if [ -f "$HOME/.bashrc" ]; then\n  . "$HOME/.bashrc"\nfi\n',
        "reason": (
            "Bash login shells fall back to `~/.profile` because neither `~/.bash_profile` nor `~/.bash_login` "
            "exists, but it does not reference `~/.bashrc`."
        ),
    }


def test_shell_bridge_recommendation_is_none_when_relative_profile_bridge_reaches_bashrc(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f .bashrc ]; then . .bashrc; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    recommendation = build_bash_login_shell_bridge_recommendation(home=home, cwd=home)

    assert recommendation is None


def test_shell_bridge_recommendation_reuses_shadowed_profile_bridge(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text('export PATH="$HOME/bin:$PATH"\n', encoding="utf-8")
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    recommendation = build_bash_login_shell_bridge_recommendation(home=home)

    assert recommendation is not None
    assert recommendation.as_dict() == {
        "target": "~/.bash_profile",
        "source": "~/.profile",
        "snippet": 'if [ -f "$HOME/.profile" ]; then\n  . "$HOME/.profile"\nfi\n',
        "reason": (
            "Bash login shells use `~/.bash_profile`, so `~/.profile` will never run even though it references "
            "`~/.bashrc`; add the same bridge to the active login file."
        ),
    }


def test_shell_bridge_recommendation_is_none_when_login_chain_already_reaches_bashrc(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    recommendation = build_bash_login_shell_bridge_recommendation(home=home)

    assert recommendation is None


def test_shell_bridge_recommendation_is_none_when_home_bashrc_is_symlinked(tmp_path: Path):
    home = tmp_path / "home"
    dotfiles = tmp_path / "dotfiles"
    home.mkdir()
    dotfiles.mkdir()
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    (dotfiles / "bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")
    (home / ".bashrc").symlink_to(dotfiles / "bashrc")

    recommendation = build_bash_login_shell_bridge_recommendation(home=home)

    assert recommendation is None


def test_shell_bridge_recommendation_is_none_when_custom_home_bridge_reaches_bashrc(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text(
        'if [ -f "$HOME/.bash_agentflow" ]; then . "$HOME/.bash_agentflow"; fi\n',
        encoding="utf-8",
    )
    (home / ".bash_agentflow").write_text(
        'if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n',
        encoding="utf-8",
    )
    (home / ".bashrc").write_text("kimi(){ :; }\n", encoding="utf-8")

    recommendation = build_bash_login_shell_bridge_recommendation(home=home)

    assert recommendation is None


def test_shell_bridge_recommendation_handles_unreadable_login_file(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    login_file = home / ".bash_profile"
    login_file.write_text('export PATH="$HOME/bin:$PATH"\n', encoding="utf-8")
    os.chmod(login_file, 0)

    recommendation = build_bash_login_shell_bridge_recommendation(home=home)

    assert recommendation is not None
    assert recommendation.as_dict() == {
        "target": "~/.bash_profile",
        "source": "~/.bashrc",
        "snippet": 'if [ -f "$HOME/.bashrc" ]; then\n  . "$HOME/.bashrc"\nfi\n',
        "reason": (
            "Bash login shells use `~/.bash_profile`, but AgentFlow could not read `~/.bash_profile` while "
            "checking whether login shells reach `~/.bashrc`: Permission denied. Add a direct bridge to the active "
            "login file."
        ),
    }
