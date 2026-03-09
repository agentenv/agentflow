from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import yaml


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}", encoding="utf-8")
    path.chmod(0o755)


def _write_fake_agentflow_module(root: Path, body: str) -> Path:
    package_dir = root / "agentflow"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "__main__.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return root


def _copy_script(source: Path, destination: Path) -> Path:
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    destination.chmod(0o755)
    return destination


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


def _run_shell(command: str, *, cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-lc", command],
        capture_output=True,
        cwd=cwd,
        env={**os.environ, **env},
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
    assert f"codex: {home / 'bin' / 'codex'} (codex-cli 0.0.0)" in completed.stdout
    assert f"claude: {home / 'bin' / 'claude'} (Claude Code 0.0.0)" in completed.stdout
    assert completed.stderr == ""


def test_make_python_target_prints_repo_python_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        ["make", "-s", "python"],
        capture_output=True,
        cwd=repo_root,
        env=os.environ,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == _repo_python(repo_root)
    assert completed.stderr == ""


def test_make_help_verify_local_mentions_bundled_run_local() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        ["make", "-s", "help"],
        capture_output=True,
        cwd=repo_root,
        env=os.environ,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0
    assert (
        "verify-local  Run the full local Codex + Claude-on-Kimi verification stack across bundled "
        "bootstrap/shell_init/target.shell inspect/doctor/smoke/run coverage, bundled "
        "toolchain-local/check-local"
    ) in completed.stdout
    assert (
        "run-local     Run the bundled local Codex + Claude-on-Kimi pipeline through `agentflow run`"
    ) in completed.stdout
    assert completed.stderr == ""


def test_make_help_mentions_bundled_kimi_variant_shortcuts() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        ["make", "-s", "help"],
        capture_output=True,
        cwd=repo_root,
        env=os.environ,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0
    assert (
        "inspect-local-shell-init Inspect the bundled local Codex + Claude-on-Kimi `shell_init: kimi` "
        "smoke pipeline"
    ) in completed.stdout
    assert (
        "inspect-local-shell-wrapper Inspect the bundled local Codex + Claude-on-Kimi `target.shell` "
        "wrapper smoke pipeline"
    ) in completed.stdout
    assert (
        "doctor-local-shell-init Check the bundled local Codex + Claude-on-Kimi `shell_init: kimi` "
        "smoke prerequisites"
    ) in completed.stdout
    assert (
        "doctor-local-shell-wrapper Check the bundled local Codex + Claude-on-Kimi `target.shell` "
        "wrapper smoke prerequisites"
    ) in completed.stdout
    assert (
        "smoke-local-shell-init Run the bundled local Codex + Claude-on-Kimi `shell_init: kimi` "
        "smoke test"
    ) in completed.stdout
    assert (
        "smoke-local-shell-wrapper Run the bundled local Codex + Claude-on-Kimi `target.shell` "
        "wrapper smoke test"
    ) in completed.stdout
    assert (
        "run-local-shell-init Run the bundled local Codex + Claude-on-Kimi `shell_init: kimi` "
        "pipeline through `agentflow run`"
    ) in completed.stdout
    assert (
        "run-local-shell-wrapper Run the bundled local Codex + Claude-on-Kimi `target.shell` "
        "wrapper pipeline through `agentflow run`"
    ) in completed.stdout
    assert completed.stderr == ""


def test_make_bundled_kimi_variant_shortcuts_point_to_shipped_examples() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [
            "make",
            "-n",
            "inspect-local-shell-init",
            "doctor-local-shell-init",
            "smoke-local-shell-init",
            "run-local-shell-init",
            "inspect-local-shell-wrapper",
            "doctor-local-shell-wrapper",
            "smoke-local-shell-wrapper",
            "run-local-shell-wrapper",
        ],
        capture_output=True,
        cwd=repo_root,
        env=os.environ,
        text=True,
        timeout=5,
    )

    python_bin = _repo_python(repo_root)
    try:
        recipe_python = str(Path(python_bin).relative_to(repo_root))
    except ValueError:
        recipe_python = python_bin

    assert completed.returncode == 0
    assert (
        f"{recipe_python} -m agentflow inspect examples/local-real-agents-kimi-shell-init-smoke.yaml --output summary"
    ) in completed.stdout
    assert (
        f"{recipe_python} -m agentflow doctor examples/local-real-agents-kimi-shell-init-smoke.yaml --output summary"
    ) in completed.stdout
    assert (
        f"{recipe_python} -m agentflow smoke examples/local-real-agents-kimi-shell-init-smoke.yaml --show-preflight"
    ) in completed.stdout
    assert (
        f"{recipe_python} -m agentflow run examples/local-real-agents-kimi-shell-init-smoke.yaml --output summary"
    ) in completed.stdout
    assert (
        f"{recipe_python} -m agentflow inspect examples/local-real-agents-kimi-shell-wrapper-smoke.yaml --output summary"
    ) in completed.stdout
    assert (
        f"{recipe_python} -m agentflow doctor examples/local-real-agents-kimi-shell-wrapper-smoke.yaml --output summary"
    ) in completed.stdout
    assert (
        f"{recipe_python} -m agentflow smoke examples/local-real-agents-kimi-shell-wrapper-smoke.yaml --show-preflight"
    ) in completed.stdout
    assert (
        f"{recipe_python} -m agentflow run examples/local-real-agents-kimi-shell-wrapper-smoke.yaml --output summary"
    ) in completed.stdout
    assert completed.stderr == ""


def test_verify_local_kimi_shell_script_requires_kimi_to_export_anthropic_env(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_fake_shell_home(home, kimi_body=":")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "verify-local-kimi-shell.sh"

    completed = _run_script(
        script_path,
        repo_root=repo_root,
        home=home,
        ANTHROPIC_API_KEY="ambient-kimi-key",
        ANTHROPIC_BASE_URL="https://api.kimi.com/coding/",
        OPENAI_API_KEY="",
    )

    assert completed.returncode == 1
    assert "~/.profile: present" in completed.stdout
    assert "kimi did not export ANTHROPIC_API_KEY" in completed.stderr


def test_verify_custom_local_kimi_shell_init_wrapper_forces_shell_init_mode(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    wrapper_path = _copy_script(
        repo_root / "scripts" / "verify-custom-local-kimi-shell-init.sh",
        scripts_dir / "verify-custom-local-kimi-shell-init.sh",
    )
    _write_executable(
        scripts_dir / "verify-custom-local-kimi-pipeline.sh",
        'printf "%s\\n" "${AGENTFLOW_KIMI_PIPELINE_MODE:-}"\n',
    )

    completed = subprocess.run(
        ["bash", str(wrapper_path)],
        capture_output=True,
        cwd=tmp_path,
        env={**os.environ, "AGENTFLOW_KIMI_PIPELINE_MODE": "shell-wrapper"},
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "shell-init"
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


def test_verify_custom_local_kimi_run_script_times_out_when_agentflow_hangs(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    run_path = _copy_script(
        repo_root / "scripts" / "verify-custom-local-kimi-run.sh",
        scripts_dir / "verify-custom-local-kimi-run.sh",
    )
    _copy_script(
        repo_root / "scripts" / "custom-local-kimi-helpers.sh",
        scripts_dir / "custom-local-kimi-helpers.sh",
    )
    fake_pythonpath = _write_fake_agentflow_module(
        tmp_path / "fake-pythonpath",
        """
        from __future__ import annotations

        import sys
        import time

        if len(sys.argv) > 1 and sys.argv[1] == "run":
            print("run-stdout", flush=True)
            print("run-stderr", file=sys.stderr, flush=True)
            time.sleep(5)
        """,
    )

    started_at = time.monotonic()
    completed = subprocess.run(
        ["bash", str(run_path)],
        capture_output=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "AGENTFLOW_PYTHON": sys.executable,
            "PYTHONPATH": str(fake_pythonpath),
            "AGENTFLOW_LOCAL_VERIFY_TIMEOUT_SECONDS": "0.2",
        },
        text=True,
        timeout=5,
    )
    elapsed = time.monotonic() - started_at

    assert completed.returncode == 124
    assert "custom run pipeline path:" in completed.stdout
    assert "Timed out after 0.2s:" in completed.stderr
    assert "agentflow run stderr:" in completed.stderr
    assert "run-stderr" in completed.stderr
    assert "agentflow run stdout:" in completed.stderr
    assert "run-stdout" in completed.stderr
    assert elapsed < 3


def test_verify_bundled_local_kimi_run_script_times_out_when_agentflow_hangs(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    run_path = _copy_script(
        repo_root / "scripts" / "verify-bundled-local-kimi-run.sh",
        scripts_dir / "verify-bundled-local-kimi-run.sh",
    )
    _copy_script(
        repo_root / "scripts" / "custom-local-kimi-helpers.sh",
        scripts_dir / "custom-local-kimi-helpers.sh",
    )
    fake_pythonpath = _write_fake_agentflow_module(
        tmp_path / "fake-pythonpath",
        """
        from __future__ import annotations

        import sys
        import time

        if len(sys.argv) > 1 and sys.argv[1] == "run":
            print("run-stdout", flush=True)
            print("run-stderr", file=sys.stderr, flush=True)
            time.sleep(5)
        """,
    )

    started_at = time.monotonic()
    completed = subprocess.run(
        ["bash", str(run_path)],
        capture_output=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "AGENTFLOW_PYTHON": sys.executable,
            "PYTHONPATH": str(fake_pythonpath),
            "AGENTFLOW_LOCAL_VERIFY_TIMEOUT_SECONDS": "0.2",
        },
        text=True,
        timeout=5,
    )
    elapsed = time.monotonic() - started_at

    bundled_smoke_pipeline = tmp_path / "examples" / "local-real-agents-kimi-smoke.yaml"

    assert completed.returncode == 124
    assert f"bundled run pipeline path: {bundled_smoke_pipeline}" in completed.stdout
    assert "Timed out after 0.2s:" in completed.stderr
    assert "agentflow run stderr:" in completed.stderr
    assert "run-stderr" in completed.stderr
    assert "agentflow run stdout:" in completed.stderr
    assert "run-stdout" in completed.stderr
    assert elapsed < 3


def test_verify_bundled_local_kimi_run_script_accepts_shell_wrapper_bundle_overrides(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    run_path = _copy_script(
        repo_root / "scripts" / "verify-bundled-local-kimi-run.sh",
        scripts_dir / "verify-bundled-local-kimi-run.sh",
    )
    _copy_script(
        repo_root / "scripts" / "custom-local-kimi-helpers.sh",
        scripts_dir / "custom-local-kimi-helpers.sh",
    )
    fake_pythonpath = _write_fake_agentflow_module(
        tmp_path / "fake-pythonpath",
        """
        from __future__ import annotations

        import json
        import sys

        if len(sys.argv) > 2 and sys.argv[1] == "run":
            payload = {
                "status": "completed",
                "pipeline": {"name": "local-real-agents-kimi-shell-wrapper-smoke"},
                "nodes": [
                    {"id": "codex_plan", "status": "completed", "preview": "codex ok"},
                    {"id": "claude_review", "status": "completed", "preview": "claude ok"},
                ],
            }
            print(json.dumps(payload), flush=True)
            print("Doctor: ok", file=sys.stderr, flush=True)
            print(
                "- bootstrap_env_override: ok - Node `claude_review`: Local shell bootstrap overrides "
                "current `ANTHROPIC_API_KEY` for this node via `target.shell` (`kimi` helper).",
                file=sys.stderr,
                flush=True,
            )
            print(
                "Pipeline auto preflight: enabled - local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.",
                file=sys.stderr,
                flush=True,
            )
            print(
                "Pipeline auto preflight matches: codex_plan (codex) via `target.shell`, "
                "claude_review (claude) via `target.shell`",
                file=sys.stderr,
                flush=True,
            )
        """,
    )

    bundled_wrapper_pipeline = tmp_path / "examples" / "local-real-agents-kimi-shell-wrapper-smoke.yaml"

    completed = subprocess.run(
        ["bash", str(run_path)],
        capture_output=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "AGENTFLOW_PYTHON": sys.executable,
            "PYTHONPATH": str(fake_pythonpath),
            "AGENTFLOW_BUNDLED_PIPELINE_PATH": str(bundled_wrapper_pipeline),
            "AGENTFLOW_BUNDLED_PIPELINE_NAME": "local-real-agents-kimi-shell-wrapper-smoke",
            "AGENTFLOW_BUNDLED_EXPECTED_TRIGGER": "target.shell",
            "AGENTFLOW_BUNDLED_EXPECTED_AUTO_PREFLIGHT_REASON": (
                "local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap."
            ),
        },
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0
    assert f"bundled run pipeline path: {bundled_wrapper_pipeline}" in completed.stdout
    assert "validated bundled agentflow run json-summary stdout and preflight stderr" in completed.stdout
    assert completed.stderr == ""


def test_verify_custom_local_kimi_pipeline_script_reports_success(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    pipeline_path = _copy_script(
        repo_root / "scripts" / "verify-custom-local-kimi-pipeline.sh",
        scripts_dir / "verify-custom-local-kimi-pipeline.sh",
    )
    _copy_script(
        repo_root / "scripts" / "custom-local-kimi-helpers.sh",
        scripts_dir / "custom-local-kimi-helpers.sh",
    )
    fake_pythonpath = _write_fake_agentflow_module(
        tmp_path / "fake-pythonpath",
        """
        from __future__ import annotations

        import json
        import sys
        from pathlib import Path

        if len(sys.argv) > 2 and sys.argv[1] == "check-local":
            pipeline_path = Path(sys.argv[2])
            pipeline_name = pipeline_path.stem

            run_payload = {
                "status": "completed",
                "pipeline": {"name": pipeline_name},
                "nodes": [
                    {"id": "codex_plan", "status": "completed", "preview": "codex ok"},
                    {"id": "claude_review", "status": "completed", "preview": "claude ok"},
                ],
            }
            preflight_payload = {
                "status": "ok",
                "checks": [
                    {"name": "bash_login_startup", "status": "ok", "detail": "ready"},
                    {"name": "kimi_shell_helper", "status": "ok", "detail": "ready"},
                    {"name": "claude_ready", "status": "ok", "detail": "ready"},
                    {"name": "codex_ready", "status": "ok", "detail": "ready"},
                    {"name": "codex_auth", "status": "ok", "detail": "ready"},
                    {
                        "name": "bootstrap_env_override",
                        "status": "ok",
                        "detail": (
                            "Node `claude_review`: Local shell bootstrap overrides current "
                            "`ANTHROPIC_API_KEY` for this node via `target.bootstrap` (`kimi` helper)."
                        ),
                    },
                ],
                "pipeline": {
                    "auto_preflight": {
                        "enabled": True,
                        "reason": "local Codex/Claude/Kimi nodes use a `kimi` shell bootstrap.",
                        "match_summary": [
                            "codex_plan (codex) via `target.bootstrap`",
                            "claude_review (claude) via `target.bootstrap`",
                        ],
                    }
                },
            }

            print(json.dumps(run_payload), flush=True)
            print(json.dumps(preflight_payload), file=sys.stderr, flush=True)
        """,
    )

    completed = subprocess.run(
        ["bash", str(pipeline_path)],
        capture_output=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "AGENTFLOW_PYTHON": sys.executable,
            "PYTHONPATH": str(fake_pythonpath),
        },
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0
    assert "custom pipeline path:" in completed.stdout
    assert "validated agentflow check-local json-summary stdout and preflight stderr" in completed.stdout
    assert completed.stderr == ""


def test_custom_local_kimi_pipeline_writers_match_bundled_examples(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    helpers_path = repo_root / "scripts" / "custom-local-kimi-helpers.sh"
    bundled_examples = {
        "bootstrap": repo_root / "examples" / "local-real-agents-kimi-smoke.yaml",
        "shell-init": repo_root / "examples" / "local-real-agents-kimi-shell-init-smoke.yaml",
        "shell-wrapper": repo_root / "examples" / "local-real-agents-kimi-shell-wrapper-smoke.yaml",
    }
    writers = {
        "bootstrap": "write_custom_local_kimi_pipeline",
        "shell-init": "write_custom_local_kimi_shell_init_pipeline",
        "shell-wrapper": "write_custom_local_kimi_shell_wrapper_pipeline",
    }

    for mode, example_path in bundled_examples.items():
        output_path = tmp_path / f"{mode}.yaml"
        completed = _run_shell(
            f'source "{helpers_path}" && {writers[mode]} "{output_path}" "{mode}-name" "{mode}-description"',
            cwd=tmp_path,
            AGENTFLOW_PYTHON=_repo_python(repo_root),
        )
        assert completed.returncode == 0, completed.stderr

        generated = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        bundled = yaml.safe_load(example_path.read_text(encoding="utf-8"))

        assert generated.pop("name") == f"{mode}-name"
        assert generated.pop("description") == f"{mode}-description"
        bundled.pop("name")
        bundled.pop("description")
        assert generated == bundled


def test_verify_local_kimi_stack_script_runs_steps_in_expected_order(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    stack_path = _copy_script(
        repo_root / "scripts" / "verify-local-kimi-stack.sh",
        scripts_dir / "verify-local-kimi-stack.sh",
    )
    _copy_script(
        repo_root / "scripts" / "custom-local-kimi-helpers.sh",
        scripts_dir / "custom-local-kimi-helpers.sh",
    )
    log_path = tmp_path / "calls.log"
    fake_pythonpath = _write_fake_agentflow_module(
        tmp_path / "fake-pythonpath",
        """
        from __future__ import annotations

        import os
        import sys

        with open(os.environ["AGENTFLOW_TEST_LOG"], "a", encoding="utf-8") as handle:
            handle.write(f"agentflow:{' '.join(sys.argv[1:])}\\n")
        """,
    )

    for script_name in (
        "verify-local-kimi-shell.sh",
        "verify-bundled-local-kimi-run.sh",
        "verify-custom-local-kimi-doctor.sh",
        "verify-custom-local-kimi-inspect.sh",
        "verify-custom-local-kimi-pipeline.sh",
        "verify-custom-local-kimi-shell-init.sh",
        "verify-custom-local-kimi-run.sh",
    ):
        _write_executable(
            scripts_dir / script_name,
            'printf "%s mode=%s\\n" "${0##*/}" "${AGENTFLOW_KIMI_PIPELINE_MODE:-}" >>"$AGENTFLOW_TEST_LOG"\n',
        )

    completed = subprocess.run(
        ["bash", str(stack_path)],
        capture_output=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "AGENTFLOW_PYTHON": sys.executable,
            "AGENTFLOW_TEST_LOG": str(log_path),
            "PYTHONPATH": str(fake_pythonpath),
        },
        text=True,
        timeout=5,
    )

    bundled_smoke_pipeline = tmp_path / "examples" / "local-real-agents-kimi-smoke.yaml"

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "verify-local-kimi-shell.sh mode=",
        "agentflow:toolchain-local --output summary",
        f"agentflow:inspect {bundled_smoke_pipeline} --output summary",
        f"agentflow:doctor {bundled_smoke_pipeline} --output summary",
        f"agentflow:smoke {bundled_smoke_pipeline} --output summary",
        "verify-bundled-local-kimi-run.sh mode=",
        f"agentflow:inspect {tmp_path / 'examples' / 'local-real-agents-kimi-shell-init-smoke.yaml'} --output summary",
        f"agentflow:doctor {tmp_path / 'examples' / 'local-real-agents-kimi-shell-init-smoke.yaml'} --output summary",
        f"agentflow:smoke {tmp_path / 'examples' / 'local-real-agents-kimi-shell-init-smoke.yaml'} --output summary",
        "verify-bundled-local-kimi-run.sh mode=",
        f"agentflow:inspect {tmp_path / 'examples' / 'local-real-agents-kimi-shell-wrapper-smoke.yaml'} --output summary",
        f"agentflow:doctor {tmp_path / 'examples' / 'local-real-agents-kimi-shell-wrapper-smoke.yaml'} --output summary",
        f"agentflow:smoke {tmp_path / 'examples' / 'local-real-agents-kimi-shell-wrapper-smoke.yaml'} --output summary",
        "verify-bundled-local-kimi-run.sh mode=",
        "verify-custom-local-kimi-doctor.sh mode=",
        "verify-custom-local-kimi-doctor.sh mode=shell-init",
        "verify-custom-local-kimi-doctor.sh mode=shell-wrapper",
        "verify-custom-local-kimi-inspect.sh mode=",
        "verify-custom-local-kimi-inspect.sh mode=shell-init",
        "verify-custom-local-kimi-inspect.sh mode=shell-wrapper",
        "agentflow:check-local --output summary",
        "verify-custom-local-kimi-pipeline.sh mode=",
        "verify-custom-local-kimi-shell-init.sh mode=",
        "verify-custom-local-kimi-pipeline.sh mode=shell-wrapper",
        "verify-custom-local-kimi-run.sh mode=",
        "verify-custom-local-kimi-run.sh mode=shell-init",
        "verify-custom-local-kimi-run.sh mode=shell-wrapper",
    ]
    assert completed.stdout.count("== ") == 27
    assert "== Shell toolchain ==" in completed.stdout
    assert "== Bundled toolchain-local ==" in completed.stdout
    assert "== Bundled inspect-local ==" in completed.stdout
    assert "== Bundled doctor-local ==" in completed.stdout
    assert "== Bundled smoke-local ==" in completed.stdout
    assert "== Bundled run-local ==" in completed.stdout
    assert "== Bundled inspect-local (shell_init) ==" in completed.stdout
    assert "== Bundled smoke-local (shell_init) ==" in completed.stdout
    assert "== Bundled run-local (shell_init) ==" in completed.stdout
    assert "== Bundled inspect-local (target.shell) ==" in completed.stdout
    assert "== Bundled smoke-local (target.shell) ==" in completed.stdout
    assert "== Bundled run-local (target.shell) ==" in completed.stdout
    assert "== Bundled check-local ==" in completed.stdout
    assert "== External custom run (target.shell) ==" in completed.stdout
