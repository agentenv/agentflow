from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentflow.audit.intake import build_report_manifest, load_manifest


def _write_manifest(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_manifest_accepts_local_source_and_uses_report_safe_scope(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    manifest_path = tmp_path / "local.json"
    _write_manifest(
        manifest_path,
        {
            "target": {
                "source": {"kind": "local", "local_path": str(source_dir)},
                "report": {"project_name": "Example Vault", "audit_scope": "src/contracts/vault"},
                "chain_context": {
                    "chain": "ethereum",
                    "contract_address_url": "https://etherscan.io/address/0x1234",
                    "creation_tx_url": "https://etherscan.io/tx/0xabcd",
                },
            },
            "run": {"artifacts_dir": ".agentflow/audits/example-vault", "parallel_shards": 6},
            "policy": {"allow_source_confirmed_without_poc": True, "max_poc_candidates": 5},
        },
    )

    manifest = load_manifest(manifest_path)
    report_manifest = build_report_manifest(manifest, source_identifier="snapshot:deadbeef")

    assert manifest.target.source.kind == "local"
    assert report_manifest.audit_scope == "src/contracts/vault"
    assert report_manifest.contract_address_url == "https://etherscan.io/address/0x1234"
    assert str(source_dir) not in report_manifest.model_dump_json()


def test_load_manifest_requires_commit_for_github_sources(tmp_path: Path) -> None:
    manifest_path = tmp_path / "github.json"
    _write_manifest(
        manifest_path,
        {
            "target": {
                "source": {"kind": "github", "repo_url": "https://github.com/example/vault"},
                "report": {"project_name": "Example Vault", "audit_scope": "src/contracts/vault"},
            },
            "run": {"artifacts_dir": ".agentflow/audits/example-vault", "parallel_shards": 6},
            "policy": {"allow_source_confirmed_without_poc": True, "max_poc_candidates": 5},
        },
    )

    with pytest.raises(ValueError, match="commit"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_absolute_report_scope(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    manifest_path = tmp_path / "unsafe.json"
    _write_manifest(
        manifest_path,
        {
            "target": {
                "source": {"kind": "local", "local_path": str(source_dir)},
                "report": {"project_name": "Example Vault", "audit_scope": str(source_dir)},
            },
            "run": {"artifacts_dir": ".agentflow/audits/example-vault", "parallel_shards": 6},
            "policy": {"allow_source_confirmed_without_poc": True, "max_poc_candidates": 5},
        },
    )

    with pytest.raises(ValueError, match="audit_scope"):
        load_manifest(manifest_path)


def test_load_manifest_resolves_relative_local_path_from_manifest_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_dir = tmp_path / "manifests" / "source"
    source_dir.mkdir(parents=True)
    manifest_path = tmp_path / "manifests" / "local-relative.json"
    _write_manifest(
        manifest_path,
        {
            "target": {
                "source": {"kind": "local", "local_path": "./source"},
                "report": {"project_name": "Example Vault", "audit_scope": "src/contracts/vault"},
            },
            "run": {"artifacts_dir": ".agentflow/audits/example-vault", "parallel_shards": 6},
            "policy": {"allow_source_confirmed_without_poc": True, "max_poc_candidates": 5},
        },
    )

    monkeypatch.chdir(tmp_path)

    manifest = load_manifest(manifest_path)

    assert manifest.target.source.kind == "local"
    assert manifest.target.source.local_path == source_dir.resolve()


@pytest.mark.parametrize("unsafe_scope", ["src/../secrets", "foo/../../bar"])
def test_load_manifest_rejects_audit_scope_path_traversal(tmp_path: Path, unsafe_scope: str) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    manifest_path = tmp_path / "unsafe-traversal.json"
    _write_manifest(
        manifest_path,
        {
            "target": {
                "source": {"kind": "local", "local_path": str(source_dir)},
                "report": {"project_name": "Example Vault", "audit_scope": unsafe_scope},
            },
            "run": {"artifacts_dir": ".agentflow/audits/example-vault", "parallel_shards": 6},
            "policy": {"allow_source_confirmed_without_poc": True, "max_poc_candidates": 5},
        },
    )

    with pytest.raises(ValueError, match="audit_scope"):
        load_manifest(manifest_path)


@pytest.mark.parametrize("unsafe_scope", [r"C:\Users\alice\contracts", r"\\server\share\contracts"])
def test_load_manifest_rejects_windows_style_absolute_audit_scope(tmp_path: Path, unsafe_scope: str) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    manifest_path = tmp_path / "unsafe-windows-absolute.json"
    _write_manifest(
        manifest_path,
        {
            "target": {
                "source": {"kind": "local", "local_path": str(source_dir)},
                "report": {"project_name": "Example Vault", "audit_scope": unsafe_scope},
            },
            "run": {"artifacts_dir": ".agentflow/audits/example-vault", "parallel_shards": 6},
            "policy": {"allow_source_confirmed_without_poc": True, "max_poc_candidates": 5},
        },
    )

    with pytest.raises(ValueError, match="audit_scope"):
        load_manifest(manifest_path)


@pytest.mark.parametrize("commit_value", ["", "   ", "\n\t"])
def test_load_manifest_rejects_blank_commit_for_github_source(tmp_path: Path, commit_value: str) -> None:
    manifest_path = tmp_path / "github-blank-commit.json"
    _write_manifest(
        manifest_path,
        {
            "target": {
                "source": {
                    "kind": "github",
                    "repo_url": "https://github.com/example/vault",
                    "commit": commit_value,
                },
                "report": {"project_name": "Example Vault", "audit_scope": "src/contracts/vault"},
            },
            "run": {"artifacts_dir": ".agentflow/audits/example-vault", "parallel_shards": 6},
            "policy": {"allow_source_confirmed_without_poc": True, "max_poc_candidates": 5},
        },
    )

    with pytest.raises(ValueError, match="commit"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_non_github_repo_url_for_github_source(tmp_path: Path) -> None:
    manifest_path = tmp_path / "github-local-path.json"
    _write_manifest(
        manifest_path,
        {
            "target": {
                "source": {
                    "kind": "github",
                    "repo_url": str(tmp_path / "not-github"),
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                },
                "report": {"project_name": "Example Vault", "audit_scope": "src/contracts/vault"},
            },
            "run": {"artifacts_dir": ".agentflow/audits/example-vault", "parallel_shards": 6},
            "policy": {"allow_source_confirmed_without_poc": True, "max_poc_candidates": 5},
        },
    )

    with pytest.raises(ValueError, match="github.com"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_host_only_github_repo_url(tmp_path: Path) -> None:
    manifest_path = tmp_path / "github-host-only.json"
    _write_manifest(
        manifest_path,
        {
            "target": {
                "source": {
                    "kind": "github",
                    "repo_url": "https://github.com",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                },
                "report": {"project_name": "Example Vault", "audit_scope": "src/contracts/vault"},
            },
            "run": {"artifacts_dir": ".agentflow/audits/example-vault", "parallel_shards": 6},
            "policy": {"allow_source_confirmed_without_poc": True, "max_poc_candidates": 5},
        },
    )

    with pytest.raises(ValueError, match="github.com/.+/.+"):
        load_manifest(manifest_path)


def test_load_manifest_accepts_absolute_artifacts_dir(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    artifacts_dir = tmp_path / "artifacts"
    manifest_path = tmp_path / "absolute-artifacts.json"
    _write_manifest(
        manifest_path,
        {
            "target": {
                "source": {"kind": "local", "local_path": str(source_dir)},
                "report": {"project_name": "Example Vault", "audit_scope": "src/contracts/vault"},
            },
            "run": {"artifacts_dir": str(artifacts_dir), "parallel_shards": 6},
            "policy": {"allow_source_confirmed_without_poc": True, "max_poc_candidates": 5},
        },
    )

    manifest = load_manifest(manifest_path)

    assert manifest.run.artifacts_dir == str(artifacts_dir)


def test_load_manifest_rejects_artifacts_dir_path_traversal(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    manifest_path = tmp_path / "traversal-artifacts.json"
    _write_manifest(
        manifest_path,
        {
            "target": {
                "source": {"kind": "local", "local_path": str(source_dir)},
                "report": {"project_name": "Example Vault", "audit_scope": "src/contracts/vault"},
            },
            "run": {"artifacts_dir": "../outside", "parallel_shards": 6},
            "policy": {"allow_source_confirmed_without_poc": True, "max_poc_candidates": 5},
        },
    )

    with pytest.raises(ValueError, match="artifacts_dir"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_file_local_path(tmp_path: Path) -> None:
    source_file = tmp_path / "single.sol"
    source_file.write_text("contract Vault {}", encoding="utf-8")
    manifest_path = tmp_path / "file-local-path.json"
    _write_manifest(
        manifest_path,
        {
            "target": {
                "source": {"kind": "local", "local_path": str(source_file)},
                "report": {"project_name": "Example Vault", "audit_scope": "src/contracts/vault"},
            },
            "run": {"artifacts_dir": ".agentflow/audits/example-vault", "parallel_shards": 6},
            "policy": {"allow_source_confirmed_without_poc": True, "max_poc_candidates": 5},
        },
    )

    with pytest.raises(ValueError, match="local_path"):
        load_manifest(manifest_path)


@pytest.mark.parametrize("bad_url", ["/Users/alice/private/debug.txt", "file:///Users/alice/private/address.txt"])
def test_load_manifest_rejects_non_http_chain_context_urls(tmp_path: Path, bad_url: str) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    manifest_path = tmp_path / "bad-chain-context.json"
    _write_manifest(
        manifest_path,
        {
            "target": {
                "source": {"kind": "local", "local_path": str(source_dir)},
                "report": {"project_name": "Example Vault", "audit_scope": "src/contracts/vault"},
                "chain_context": {
                    "chain": "ethereum",
                    "contract_address_url": bad_url,
                    "creation_tx_url": "https://etherscan.io/tx/0x1234",
                },
            },
            "run": {"artifacts_dir": ".agentflow/audits/example-vault", "parallel_shards": 6},
            "policy": {"allow_source_confirmed_without_poc": True, "max_poc_candidates": 5},
        },
    )

    with pytest.raises(ValueError, match="http"):
        load_manifest(manifest_path)
