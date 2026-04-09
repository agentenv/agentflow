from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentflow.audit.materialize import _inventory_files, _snapshot_digest, materialize_source
from agentflow.audit.models import (
    ContractAuditManifest,
    LocalSourceConfig,
    PolicyConfig,
    RunConfig,
    TargetConfig,
    TargetReportConfig,
)


def _build_local_manifest(local_path: Path) -> ContractAuditManifest:
    return ContractAuditManifest(
        target=TargetConfig(
            source=LocalSourceConfig(kind="local", local_path=local_path),
            report=TargetReportConfig(project_name="Vault", audit_scope="src"),
        ),
        run=RunConfig(artifacts_dir=".agentflow/audits/vault"),
        policy=PolicyConfig(),
    )


def _run_git(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_materialize_local_source_creates_snapshot_and_snapshot_identifier(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    (source_root / "src").mkdir(parents=True)
    (source_root / "src" / "Vault.sol").write_text("contract Vault {}", encoding="utf-8")
    (source_root / "src" / "vault-link.sol").symlink_to("Vault.sol")
    (source_root / ".git").mkdir()
    (source_root / ".git" / "ignored").write_text("x", encoding="utf-8")

    manifest = _build_local_manifest(source_root)
    run_root = tmp_path / "run"

    materialized = materialize_source(manifest, run_root)

    assert materialized.snapshot_dir == run_root / "workspace" / "source_snapshot"
    assert (materialized.snapshot_dir / "src" / "Vault.sol").read_text(encoding="utf-8") == "contract Vault {}"
    assert (materialized.snapshot_dir / "src" / "vault-link.sol").is_symlink()
    assert materialized.source_mode == "local"
    assert materialized.source_identifier.startswith("snapshot:")
    assert len(materialized.source_identifier) == len("snapshot:") + 12
    assert materialized.source_inventory == ["src/Vault.sol", "src/vault-link.sol"]


def test_inventory_files_excludes_nested_git_content(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "src").mkdir(parents=True)
    (root / "src" / "A.sol").write_text("a", encoding="utf-8")
    (root / "vendor" / "pkg" / ".git").mkdir(parents=True)
    (root / "vendor" / "pkg" / ".git" / "config").write_text("secret", encoding="utf-8")

    inventory = _inventory_files(root)

    assert inventory == ["src/A.sol"]


def test_materialize_local_source_rejects_external_symlink(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    (source_root / "src").mkdir(parents=True)
    (tmp_path / "host-secret.txt").write_text("host secret", encoding="utf-8")
    (source_root / "src" / "host-secret-link").symlink_to(tmp_path / "host-secret.txt")

    manifest = _build_local_manifest(source_root)

    with pytest.raises(ValueError, match="outside"):
        materialize_source(manifest, tmp_path / "run")


def test_materialize_local_source_rejects_artifacts_root_inside_source_tree(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    (source_root / "src").mkdir(parents=True)
    (source_root / "src" / "Vault.sol").write_text("contract Vault {}", encoding="utf-8")

    manifest = _build_local_manifest(source_root)

    with pytest.raises(ValueError, match="inside the local source tree"):
        materialize_source(manifest, source_root / ".agentflow" / "audits" / "example-vault")


def test_snapshot_digest_does_not_depend_on_external_symlink_target_contents(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "src").mkdir(parents=True)
    external_target = tmp_path / "external.sol"
    external_target.write_text("v1", encoding="utf-8")
    (root / "src" / "external-link.sol").symlink_to(external_target)

    digest_before = _snapshot_digest(root)
    external_target.write_text("v2", encoding="utf-8")
    digest_after = _snapshot_digest(root)

    assert digest_before == digest_after


def test_materialize_github_source_checks_out_exact_commit_and_uses_commit_identifier(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _run_git(["init"], cwd=repo_root)
    _run_git(["config", "user.name", "Test User"], cwd=repo_root)
    _run_git(["config", "user.email", "test@example.com"], cwd=repo_root)

    (repo_root / "Contract.sol").write_text("v1", encoding="utf-8")
    _run_git(["add", "."], cwd=repo_root)
    _run_git(["commit", "-m", "first"], cwd=repo_root)
    first_commit = _run_git(["rev-parse", "HEAD"], cwd=repo_root)

    (repo_root / "Contract.sol").write_text("v2", encoding="utf-8")
    _run_git(["add", "."], cwd=repo_root)
    _run_git(["commit", "-m", "second"], cwd=repo_root)

    manifest = ContractAuditManifest(
        target=TargetConfig(
            source={"kind": "github", "repo_url": str(repo_root), "commit": first_commit},
            report=TargetReportConfig(project_name="Vault", audit_scope="src"),
        ),
        run=RunConfig(artifacts_dir=".agentflow/audits/vault"),
        policy=PolicyConfig(),
    )
    run_root = tmp_path / "run"

    materialized = materialize_source(manifest, run_root)

    assert (materialized.snapshot_dir / "Contract.sol").read_text(encoding="utf-8") == "v1"
    assert _run_git(["rev-parse", "HEAD"], cwd=run_root / "workspace" / "source_clone") == first_commit
    assert materialized.source_mode == "github"
    assert materialized.source_identifier == first_commit
    assert materialized.source_inventory == ["Contract.sol"]
