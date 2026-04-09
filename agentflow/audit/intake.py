from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from agentflow.audit.models import ContractAuditManifest, ReportManifest


def load_manifest(path: str | Path) -> ContractAuditManifest:
    manifest_path = Path(path).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = ContractAuditManifest.model_validate(payload)
    if manifest.target.source.kind == "local":
        source_path = manifest.target.source.local_path
        if not source_path.is_absolute():
            source_path = (manifest_path.parent / source_path).resolve()
        else:
            source_path = source_path.resolve()
        manifest.target.source.local_path = source_path
        if not source_path.exists():
            raise ValueError(f"local_path does not exist: {source_path}")
        if not source_path.is_dir():
            raise ValueError(f"local_path must point to a directory: {source_path}")
    else:
        repo_url = manifest.target.source.repo_url
        parsed = urlparse(repo_url)
        if parsed.scheme != "https" or parsed.netloc.lower() not in {"github.com", "www.github.com"}:
            raise ValueError("repo_url must use https://github.com/... for github sources")
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) < 2:
            raise ValueError("repo_url must use https://github.com/<owner>/<repo> for github sources")
    return manifest


def build_report_manifest(
    manifest: ContractAuditManifest, *, source_identifier: str
) -> ReportManifest:
    source = manifest.target.source
    source_mode = "local snapshot" if source.kind == "local" else "github repo"
    return ReportManifest(
        project_name=manifest.target.report.project_name,
        audit_scope=manifest.target.report.audit_scope,
        source_mode=source_mode,
        source_identifier=source_identifier,
        chain=manifest.target.chain_context.chain,
        contract_address_url=manifest.target.chain_context.contract_address_url,
        creation_tx_url=manifest.target.chain_context.creation_tx_url,
    )


def emit_normalized_manifest(manifest_path: str | Path) -> None:
    manifest = load_manifest(manifest_path)
    print(manifest.model_dump_json(indent=2))
