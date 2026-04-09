from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from agentflow.audit.pipeline_builder import AUDIT_MANIFEST_ENV, AUDIT_TRACKS, build_contract_audit_graph
from agentflow.loader import load_pipeline_from_text


def test_build_contract_audit_graph_contains_expected_nodes(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        """{
          "target": {
            "source": {"kind": "local", "local_path": "."},
            "report": {"project_name": "Example Vault", "audit_scope": "src/contracts/vault"}
          },
          "run": {"artifacts_dir": ".agentflow/audits/example-vault", "parallel_shards": 6},
          "policy": {"allow_source_confirmed_without_poc": true, "max_poc_candidates": 5}
        }""",
        encoding="utf-8",
    )

    graph = build_contract_audit_graph(str(manifest_path))
    payload = graph.to_payload()
    node_ids = [node["id"] for node in payload["nodes"]]

    assert payload["name"] == "contract-audit-example"
    assert node_ids == [
        "intake_target",
        "materialize_target",
        "prepare_foundry_workspace",
        "surface_map",
        "audit_shard",
        "finding_reduce",
        "evidence_review",
        "evidence_gate",
        "poc_author",
        "poc_verify",
        "final_adjudication",
        "report_build",
        "publish_artifacts",
    ]

    audit_shard = next(node for node in payload["nodes"] if node["id"] == "audit_shard")
    assert audit_shard["fanout"]["values"] == [{"track": track} for track in AUDIT_TRACKS]
    assert "entry-point-analyzer::default" in audit_shard["skills"]
    assert "static-analysis::default" in audit_shard["skills"]


def test_builder_exports_stable_manifest_env_name() -> None:
    assert AUDIT_MANIFEST_ENV == "AGENTFLOW_CONTRACT_AUDIT_MANIFEST"


def test_validated_pipeline_uses_expanded_merge_node_reference_and_shared_filesystem(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "target": {
                    "source": {
                        "kind": "github",
                        "repo_url": "https://github.com/example/contracts",
                        "commit": "0123456789abcdef0123456789abcdef01234567",
                    },
                    "report": {
                        "project_name": "Example Vault",
                        "audit_scope": "src/contracts/vault",
                    },
                },
                "run": {
                    "artifacts_dir": ".agentflow/audits/example-vault",
                    "parallel_shards": 6,
                },
                "policy": {
                    "allow_source_confirmed_without_poc": True,
                    "max_poc_candidates": 5,
                },
            }
        ),
        encoding="utf-8",
    )

    graph = build_contract_audit_graph(str(manifest_path))
    spec = load_pipeline_from_text(graph.to_json(), base_dir=tmp_path)

    assert spec.use_worktree is False
    assert "finding_reduce_0" in spec.node_map
    assert "{{ nodes.finding_reduce_0.output }}" in spec.node_map["evidence_review"].prompt
    assert "{{ nodes.finding_reduce.output }}" not in spec.node_map["evidence_review"].prompt


def test_public_example_prints_contract_audit_graph(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "target": {
                    "source": {
                        "kind": "github",
                        "repo_url": "https://github.com/example/contracts",
                        "commit": "0123456789abcdef0123456789abcdef01234567",
                    },
                    "report": {
                        "project_name": "Example Vault",
                        "audit_scope": "src/contracts/vault",
                    },
                    "chain_context": {
                        "chain": "ethereum",
                        "contract_address_url": "https://etherscan.io/address/0x1111111111111111111111111111111111111111",
                        "creation_tx_url": "https://etherscan.io/tx/0x2222222222222222222222222222222222222222222222222222222222222222",
                    },
                },
                "run": {
                    "artifacts_dir": ".agentflow/audits/example-vault",
                    "parallel_shards": 6,
                },
                "policy": {
                    "allow_source_confirmed_without_poc": True,
                    "max_poc_candidates": 5,
                },
            }
        ),
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env[AUDIT_MANIFEST_ENV] = str(manifest_path)

    completed = subprocess.run(
        [sys.executable, str(repo_root / "examples" / "contract_audit.py")],
        check=True,
        capture_output=True,
        text=True,
        cwd=repo_root,
        env=env,
    )

    payload = json.loads(completed.stdout)
    assert payload["name"] == "contract-audit-example"


def test_public_example_resolves_repo_root_working_dir_for_python_utility_nodes() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pipeline_path = repo_root / "examples" / "contract_audit.py"

    completed = subprocess.run(
        [sys.executable, str(pipeline_path)],
        check=False,
        capture_output=True,
        text=True,
        cwd=repo_root,
        env={**os.environ, AUDIT_MANIFEST_ENV: str(repo_root / "examples" / "contract_audit_manifest.example.json")},
    )

    assert completed.returncode == 0, completed.stderr

    spec = load_pipeline_from_text(
        completed.stdout,
        base_dir=pipeline_path.parent,
    )

    assert spec.working_dir == str(repo_root.resolve())


def test_publish_artifacts_summary_uses_report_relative_paths() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = Path(tmp) / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "target": {
                        "source": {
                            "kind": "github",
                            "repo_url": "https://github.com/example/contracts",
                            "commit": "0123456789abcdef0123456789abcdef01234567",
                        },
                        "report": {
                            "project_name": "Example Vault",
                            "audit_scope": "src/contracts/vault",
                        },
                    },
                    "run": {
                        "artifacts_dir": ".agentflow/audits/example-vault",
                        "parallel_shards": 6,
                    },
                    "policy": {
                        "allow_source_confirmed_without_poc": True,
                        "max_poc_candidates": 5,
                    },
                }
            ),
            encoding="utf-8",
        )

        graph = build_contract_audit_graph(str(manifest_path))
        payload = graph.to_payload()
        publish_artifacts = next(node for node in payload["nodes"] if node["id"] == "publish_artifacts")

        completed = subprocess.run(
            [sys.executable, "-c", publish_artifacts["prompt"]],
            check=False,
            capture_output=True,
            text=True,
            cwd=repo_root,
        )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "artifacts_root": "run.artifacts_dir",
        "report_dir": "report",
        "report": "report/AUDIT_REPORT.md",
        "findings": "report/findings.json",
        "summary": "report/audit_summary.json",
    }


def test_public_example_does_not_inline_manifest_path_in_emitted_graph_json(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "target": {
                    "source": {
                        "kind": "github",
                        "repo_url": "https://github.com/example/contracts",
                        "commit": "0123456789abcdef0123456789abcdef01234567",
                    },
                    "report": {
                        "project_name": "Example Vault",
                        "audit_scope": "src/contracts/vault",
                    },
                },
                "run": {
                    "artifacts_dir": ".agentflow/audits/example-vault",
                    "parallel_shards": 6,
                },
                "policy": {
                    "allow_source_confirmed_without_poc": True,
                    "max_poc_candidates": 5,
                },
            }
        ),
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[1]
    pipeline_path = repo_root / "examples" / "contract_audit.py"

    completed = subprocess.run(
        [sys.executable, str(pipeline_path)],
        check=False,
        capture_output=True,
        text=True,
        cwd=repo_root,
        env={**os.environ, AUDIT_MANIFEST_ENV: str(manifest_path)},
    )

    assert completed.returncode == 0, completed.stderr
    assert str(manifest_path.resolve()) not in completed.stdout


def test_build_contract_audit_graph_limits_tracks_with_parallel_shards(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "target": {
                    "source": {
                        "kind": "github",
                        "repo_url": "https://github.com/example/contracts",
                        "commit": "0123456789abcdef0123456789abcdef01234567",
                    },
                    "report": {
                        "project_name": "Example Vault",
                        "audit_scope": "src/contracts/vault",
                    },
                },
                "run": {
                    "artifacts_dir": ".agentflow/audits/example-vault",
                    "parallel_shards": 2,
                },
                "policy": {
                    "allow_source_confirmed_without_poc": True,
                    "max_poc_candidates": 5,
                },
            }
        ),
        encoding="utf-8",
    )

    payload = build_contract_audit_graph(str(manifest_path)).to_payload()
    audit_shard = next(node for node in payload["nodes"] if node["id"] == "audit_shard")
    finding_reduce = next(node for node in payload["nodes"] if node["id"] == "finding_reduce")

    assert payload["concurrency"] == 2
    assert audit_shard["fanout"]["values"] == [
        {"track": "access-control-and-init"},
        {"track": "accounting-and-rounding"},
    ]
    assert finding_reduce["fanout"]["batches"]["size"] == 2


def test_build_contract_audit_graph_threads_policy_and_locks_poc_author_cwd(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "target": {
                    "source": {
                        "kind": "github",
                        "repo_url": "https://github.com/example/contracts",
                        "commit": "0123456789abcdef0123456789abcdef01234567",
                    },
                    "report": {
                        "project_name": "Example Vault",
                        "audit_scope": "src/contracts/vault",
                    },
                },
                "run": {
                    "artifacts_dir": ".agentflow/audits/example-vault",
                    "parallel_shards": 4,
                },
                "policy": {
                    "allow_source_confirmed_without_poc": False,
                    "max_poc_candidates": 2,
                },
            }
        ),
        encoding="utf-8",
    )

    spec = build_contract_audit_graph(str(manifest_path)).to_spec()

    assert spec.node_map["poc_author"].target.cwd == ".agentflow/audits/example-vault/workspace/foundry_project"
    assert "allow_source_confirmed_without_poc=False" in spec.node_map["evidence_gate"].prompt
    assert "max_poc_candidates=2" in spec.node_map["poc_author"].prompt


def test_build_contract_audit_graph_accepts_absolute_artifacts_dir_for_poc_workspace(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    artifacts_dir = tmp_path / "cap-vault-reports" / "artifacts"
    manifest_path.write_text(
        json.dumps(
            {
                "target": {
                    "source": {
                        "kind": "github",
                        "repo_url": "https://github.com/example/contracts",
                        "commit": "0123456789abcdef0123456789abcdef01234567",
                    },
                    "report": {
                        "project_name": "Example Vault",
                        "audit_scope": "src/contracts/vault",
                    },
                },
                "run": {
                    "artifacts_dir": str(artifacts_dir),
                    "parallel_shards": 4,
                },
                "policy": {
                    "allow_source_confirmed_without_poc": True,
                    "max_poc_candidates": 3,
                },
            }
        ),
        encoding="utf-8",
    )

    spec = build_contract_audit_graph(str(manifest_path)).to_spec()

    assert spec.node_map["poc_author"].target.cwd == str(
        artifacts_dir / "workspace" / "foundry_project"
    )


def test_python_utility_node_prompts_compile_without_indentation_errors(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    artifacts_dir = tmp_path / "cap-vault-reports" / "artifacts"
    manifest_path.write_text(
        json.dumps(
            {
                "target": {
                    "source": {
                        "kind": "local",
                        "local_path": str(tmp_path),
                    },
                    "report": {
                        "project_name": "Example Vault",
                        "audit_scope": "src/contracts/vault",
                    },
                },
                "run": {
                    "artifacts_dir": str(artifacts_dir),
                    "parallel_shards": 2,
                },
                "policy": {
                    "allow_source_confirmed_without_poc": True,
                    "max_poc_candidates": 2,
                },
            }
        ),
        encoding="utf-8",
    )

    payload = build_contract_audit_graph(str(manifest_path)).to_payload()

    for node in payload["nodes"]:
        if node["agent"] != "python":
            continue
        compile(node["prompt"], f"{node['id']}.py", "exec")


def test_contract_audit_codex_nodes_bypass_local_codex_sandbox(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "target": {
                    "source": {
                        "kind": "github",
                        "repo_url": "https://github.com/example/contracts",
                        "commit": "0123456789abcdef0123456789abcdef01234567",
                    },
                    "report": {
                        "project_name": "Example Vault",
                        "audit_scope": "src/contracts/vault",
                    },
                },
                "run": {
                    "artifacts_dir": str(tmp_path / "artifacts"),
                    "parallel_shards": 3,
                },
                "policy": {
                    "allow_source_confirmed_without_poc": True,
                    "max_poc_candidates": 2,
                },
            }
        ),
        encoding="utf-8",
    )

    payload = build_contract_audit_graph(str(manifest_path)).to_payload()

    for node in payload["nodes"]:
        if node["agent"] != "codex":
            continue
        assert node["extra_args"] == ["--dangerously-bypass-approvals-and-sandbox"]
