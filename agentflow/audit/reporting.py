from __future__ import annotations

import json
import re
from pathlib import Path, PureWindowsPath

from agentflow.audit.models import FindingRecord, ReportManifest

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_SEVERITY_LEVELS = ("critical", "high", "medium", "low", "info")
_VALIDATION_LABELS = {
    "poc_confirmed": "PoC Confirmed",
    "source_confirmed": "Source Confirmed",
    "rejected": "Rejected",
}
_FREE_TEXT_PATH_PATTERNS = (
    re.compile(r'(^|[\s([{"\'])((?:[A-Za-z]:\\|\\\\)[^\s]+)', re.MULTILINE),
    re.compile(r'(^|[\s([{"\'])(/[^\s]+)', re.MULTILINE),
)
_TRAILING_PATH_PUNCTUATION = ".,:;)]}"


def sort_findings(findings: list[FindingRecord]) -> list[FindingRecord]:
    return sorted(findings, key=lambda finding: (_SEVERITY_ORDER[finding.severity], finding.id))


def _safe_component_file(path: str) -> str:
    posix_path = Path(path)
    windows_path = PureWindowsPath(path)
    is_absolute = posix_path.is_absolute() or windows_path.is_absolute()
    has_parent_traversal = ".." in posix_path.parts or ".." in windows_path.parts
    if is_absolute or has_parent_traversal:
        if "\\" in path:
            return windows_path.name
        return posix_path.name
    return path


def _sanitize_text_paths(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        prefix = match.group(1)
        raw_path = match.group(2)
        trimmed = raw_path
        trailing = ""
        while trimmed and trimmed[-1] in _TRAILING_PATH_PUNCTUATION:
            trailing = trimmed[-1] + trailing
            trimmed = trimmed[:-1]
        return prefix + _safe_component_file(trimmed) + trailing

    sanitized = text
    for pattern in _FREE_TEXT_PATH_PATTERNS:
        sanitized = pattern.sub(_replace, sanitized)
    return sanitized


def extract_json_document(text: str) -> object:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return payload
    raise ValueError("no JSON document found in text")


def public_findings_projection(findings: list[FindingRecord]) -> list[dict[str, object]]:
    projected: list[dict[str, object]] = []
    for finding in sort_findings(findings):
        payload = finding.model_dump(mode="json")
        payload["title"] = _sanitize_text_paths(str(payload["title"]))
        payload["summary"] = _sanitize_text_paths(str(payload["summary"]))
        payload["root_cause"] = _sanitize_text_paths(str(payload["root_cause"]))
        payload["attack_scenario"] = _sanitize_text_paths(str(payload["attack_scenario"]))
        payload["review"] = {
            **dict(payload["review"]),
            "notes": _sanitize_text_paths(str(payload["review"]["notes"])),
        }
        component = dict(payload["component"])
        component["file"] = _safe_component_file(str(component["file"]))
        payload["component"] = component
        evidence_items = [dict(item) for item in payload["evidence"]]
        for evidence_item in evidence_items:
            evidence_item["file"] = _safe_component_file(str(evidence_item["file"]))
            evidence_item["snippet_ref"] = _sanitize_text_paths(str(evidence_item["snippet_ref"]))
        payload["evidence"] = evidence_items
        poc_payload = dict(payload["poc"])
        if poc_payload.get("test_path") is not None:
            poc_payload["test_path"] = _safe_component_file(str(poc_payload["test_path"]))
        payload["poc"] = poc_payload
        projected.append(payload)
    return projected


def _severity_counts(findings: list[FindingRecord]) -> dict[str, int]:
    counts = {severity: 0 for severity in _SEVERITY_LEVELS}
    for finding in findings:
        counts[finding.severity] += 1
    return counts


def _validation_counts(findings: list[FindingRecord]) -> dict[str, int]:
    counts = {"poc_confirmed": 0, "source_confirmed": 0, "rejected": 0}
    for finding in findings:
        counts[finding.validation_status] += 1
    return counts


def render_audit_report(manifest: ReportManifest, findings: list[FindingRecord]) -> str:
    ordered_findings = sort_findings(findings)
    severity_counts = _severity_counts(ordered_findings)

    lines: list[str] = []
    lines.append(f"# {manifest.project_name} Contract Audit Report")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append(
        f"Reviewed `{manifest.audit_scope}` from `{manifest.source_identifier}` and produced {len(ordered_findings)} validated findings."
    )
    lines.append("")
    lines.append("## Audit Target")
    lines.append(f"- Source Mode: {manifest.source_mode}")
    lines.append(f"- Source Identifier: {manifest.source_identifier}")
    lines.append(f"- Audit Scope: {manifest.audit_scope}")
    if manifest.chain or manifest.contract_address_url or manifest.creation_tx_url:
        lines.append("- Chain Context:")
        if manifest.chain:
            lines.append(f"  - Chain: {manifest.chain}")
        if manifest.contract_address_url:
            lines.append(f"  - Contract Address: {manifest.contract_address_url}")
        if manifest.creation_tx_url:
            lines.append(f"  - Creation Transaction: {manifest.creation_tx_url}")
    lines.append("")
    lines.append("## Validation Model")
    lines.append("- PoC Confirmed: exploit or failing behavior is reproduced via dedicated PoC evidence.")
    lines.append("- Source Confirmed: source-level reasoning confirms exploitability without a full PoC run.")
    lines.append("- Rejected: issue candidate was reviewed and is not a valid vulnerability.")
    lines.append("")
    lines.append("## Severity Overview")
    for severity in _SEVERITY_LEVELS:
        lines.append(f"- {severity}: {severity_counts[severity]}")
    lines.append("")
    lines.append("## Findings")
    for finding in ordered_findings:
        lines.append("")
        lines.append(f"### {finding.id}")
        lines.append(f"- Severity: {finding.severity.title()}")
        lines.append(f"- Validation Status: {_VALIDATION_LABELS[finding.validation_status]}")
        component = _safe_component_file(finding.component.file)
        if finding.component.symbol:
            component = f"{component}::{finding.component.symbol}"
        lines.append(f"- Component: {component}")
        lines.append(f"- Summary: {_sanitize_text_paths(finding.summary)}")
        lines.append(f"- Impact: {_sanitize_text_paths(finding.attack_scenario)}")
        lines.append(f"- Recommendation: {_sanitize_text_paths(finding.root_cause)}")
        if finding.poc.test_path:
            lines.append(f"- PoC / Reproduction: {_safe_component_file(finding.poc.test_path)}")

    lines.append("")
    return "\n".join(lines)


def write_report_bundle(report_dir: str | Path, manifest: ReportManifest, findings: list[FindingRecord]) -> None:
    output_dir = Path(report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ordered_findings = sort_findings(findings)
    projected_findings = public_findings_projection(ordered_findings)
    report_text = render_audit_report(manifest, ordered_findings)
    summary_payload = {
        "project_name": manifest.project_name,
        "audit_scope": manifest.audit_scope,
        "source_mode": manifest.source_mode,
        "source_identifier": manifest.source_identifier,
        "total_findings": len(ordered_findings),
        "severity_counts": _severity_counts(ordered_findings),
        "validation_counts": _validation_counts(ordered_findings),
    }

    (output_dir / "AUDIT_REPORT.md").write_text(report_text, encoding="utf-8")
    (output_dir / "findings.json").write_text(
        json.dumps(projected_findings, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_dir / "audit_summary.json").write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_dir / "report_manifest.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
