from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agentflow.audit.materialize import MaterializedSource, _validate_symlinks_within_tree


@dataclass(frozen=True)
class PreparedFoundryWorkspace:
    workspace_dir: Path
    source_snapshot_dir: Path
    foundry_toml_path: Path
    remappings_path: Path | None


def _is_foundry_project(root: Path) -> bool:
    return (root / "foundry.toml").is_file()


def _write_synthesized_foundry_toml(root: Path) -> Path:
    foundry_toml_path = root / "foundry.toml"
    foundry_toml_path.write_text(
        "\n".join(
            [
                "[profile.default]",
                'src = "src"',
                'test = "test"',
                'libs = ["lib"]',
                'solc_version = "0.8.25"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return foundry_toml_path


def _write_remappings(root: Path) -> Path | None:
    lib_dir = root / "lib"
    remappings_path = root / "remappings.txt"
    if remappings_path.exists():
        return remappings_path
    if not lib_dir.is_dir():
        return None

    lines: list[str] = []
    for child in sorted(p for p in lib_dir.iterdir() if p.is_dir()):
        name = child.name
        lines.append(f"{name}/=lib/{name}/")
        if name == "openzeppelin-contracts":
            lines.append("@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/")
        elif name == "openzeppelin-contracts-upgradeable":
            lines.append(
                "@openzeppelin/contracts-upgradeable/=lib/openzeppelin-contracts-upgradeable/contracts/"
            )

    if lines:
        remappings_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return remappings_path
    return None


def _run_git(args: list[str], *, cwd: Path) -> str:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_ASKPASS"] = "true"
    completed = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def prepare_foundry_workspace(
    materialized: MaterializedSource, run_root: Path
) -> PreparedFoundryWorkspace:
    run_root = Path(run_root).resolve()
    workspace_dir = run_root / "workspace" / "foundry_project"
    _validate_symlinks_within_tree(materialized.snapshot_dir)
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    workspace_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        materialized.snapshot_dir,
        workspace_dir,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git"),
    )
    _validate_symlinks_within_tree(workspace_dir)

    foundry_toml_path = workspace_dir / "foundry.toml"
    if not _is_foundry_project(workspace_dir):
        foundry_toml_path = _write_synthesized_foundry_toml(workspace_dir)

    (workspace_dir / "test" / "security").mkdir(parents=True, exist_ok=True)
    remappings_path = _write_remappings(workspace_dir)

    _run_git(["init"], cwd=workspace_dir)
    _run_git(["config", "user.name", "AgentFlow Temp"], cwd=workspace_dir)
    _run_git(["config", "user.email", "agentflow-temp@example.com"], cwd=workspace_dir)
    _run_git(["add", "."], cwd=workspace_dir)
    _run_git(["commit", "-m", "baseline"], cwd=workspace_dir)

    return PreparedFoundryWorkspace(
        workspace_dir=workspace_dir,
        source_snapshot_dir=materialized.snapshot_dir,
        foundry_toml_path=foundry_toml_path,
        remappings_path=remappings_path,
    )
