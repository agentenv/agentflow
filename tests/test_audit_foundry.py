from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentflow.audit.foundry import prepare_foundry_workspace
from agentflow.audit.materialize import MaterializedSource


def _git(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_prepare_foundry_workspace_synthesizes_missing_files_and_creates_git_baseline(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    (snapshot / "src").mkdir(parents=True)
    (snapshot / "src" / "Vault.sol").write_text("contract Vault {}", encoding="utf-8")
    (snapshot / "src" / "vault-link.sol").symlink_to("Vault.sol")

    materialized = MaterializedSource(
        snapshot_dir=snapshot,
        source_identifier="snapshot:1234567890ab",
        source_mode="local",
        source_inventory=["src/Vault.sol"],
    )
    run_root = tmp_path / "run"

    prepared = prepare_foundry_workspace(materialized, run_root)

    assert prepared.workspace_dir == run_root / "workspace" / "foundry_project"
    assert prepared.source_snapshot_dir == snapshot
    assert prepared.foundry_toml_path.exists()
    assert prepared.remappings_path is None
    assert (prepared.workspace_dir / "test" / "security").is_dir()
    assert (prepared.workspace_dir / "src" / "vault-link.sol").is_symlink()

    foundry_toml = prepared.foundry_toml_path.read_text(encoding="utf-8")
    assert 'src = "src"' in foundry_toml
    assert 'test = "test"' in foundry_toml
    assert 'libs = ["lib"]' in foundry_toml
    assert 'solc_version = "0.8.25"' in foundry_toml

    baseline_commit = _git(["rev-parse", "HEAD"], cwd=prepared.workspace_dir)
    assert len(baseline_commit) == 40


def test_prepare_foundry_workspace_baseline_ignores_hostile_git_template_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hostile_home = tmp_path / "hostile-home"
    template_hooks = hostile_home / "template" / "hooks"
    template_hooks.mkdir(parents=True)
    pre_commit_hook = template_hooks / "pre-commit"
    pre_commit_hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    pre_commit_hook.chmod(0o755)
    (hostile_home / ".gitconfig").write_text(
        "[init]\n\ttemplateDir = ~/template\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(hostile_home))

    snapshot = tmp_path / "snapshot"
    (snapshot / "src").mkdir(parents=True)
    (snapshot / "src" / "Vault.sol").write_text("contract Vault {}", encoding="utf-8")
    materialized = MaterializedSource(
        snapshot_dir=snapshot,
        source_identifier="snapshot:1234567890ab",
        source_mode="local",
        source_inventory=["src/Vault.sol"],
    )

    prepared = prepare_foundry_workspace(materialized, tmp_path / "run")

    assert len(_git(["rev-parse", "HEAD"], cwd=prepared.workspace_dir)) == 40


def test_prepare_foundry_workspace_rejects_external_symlink(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    (snapshot / "src").mkdir(parents=True)
    (tmp_path / "host-secret.txt").write_text("host secret", encoding="utf-8")
    (snapshot / "src" / "host-secret-link").symlink_to(tmp_path / "host-secret.txt")
    materialized = MaterializedSource(
        snapshot_dir=snapshot,
        source_identifier="snapshot:1234567890ab",
        source_mode="local",
        source_inventory=["src/host-secret-link"],
    )

    with pytest.raises(ValueError, match="outside"):
        prepare_foundry_workspace(materialized, tmp_path / "run")


def test_prepare_foundry_workspace_writes_openzeppelin_remappings_when_libs_exist(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    (snapshot / "src").mkdir(parents=True)
    (snapshot / "src" / "Vault.sol").write_text("contract Vault {}", encoding="utf-8")
    (snapshot / "lib" / "openzeppelin-contracts").mkdir(parents=True)
    (snapshot / "lib" / "openzeppelin-contracts-upgradeable").mkdir(parents=True)

    materialized = MaterializedSource(
        snapshot_dir=snapshot,
        source_identifier="snapshot:feedfacedead",
        source_mode="local",
        source_inventory=[
            "src/Vault.sol",
            "lib/openzeppelin-contracts",
            "lib/openzeppelin-contracts-upgradeable",
        ],
    )

    prepared = prepare_foundry_workspace(materialized, tmp_path / "run")

    assert prepared.remappings_path is not None
    remappings = prepared.remappings_path.read_text(encoding="utf-8")
    assert "@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/" in remappings
    assert (
        "@openzeppelin/contracts-upgradeable/=lib/openzeppelin-contracts-upgradeable/contracts/"
        in remappings
    )


def test_prepare_foundry_workspace_preserves_existing_remappings_file(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    (snapshot / "src").mkdir(parents=True)
    (snapshot / "src" / "Vault.sol").write_text("contract Vault {}", encoding="utf-8")
    existing_remappings = "custom/=vendor/custom/\n"
    (snapshot / "remappings.txt").write_text(existing_remappings, encoding="utf-8")
    (snapshot / "lib" / "openzeppelin-contracts").mkdir(parents=True)

    materialized = MaterializedSource(
        snapshot_dir=snapshot,
        source_identifier="snapshot:cafefeedbeef",
        source_mode="local",
        source_inventory=["src/Vault.sol", "remappings.txt", "lib/openzeppelin-contracts"],
    )

    prepared = prepare_foundry_workspace(materialized, tmp_path / "run")

    assert prepared.remappings_path == prepared.workspace_dir / "remappings.txt"
    assert prepared.remappings_path.read_text(encoding="utf-8") == existing_remappings
