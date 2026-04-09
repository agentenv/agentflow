from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agentflow.audit.models import ContractAuditManifest


@dataclass(frozen=True)
class MaterializedSource:
    snapshot_dir: Path
    source_identifier: str
    source_mode: str
    source_inventory: list[str]


def _inventory_files(root: Path) -> list[str]:
    root = root.resolve()
    inventory: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if ".git" in rel.parts:
            continue
        inventory.append(rel.as_posix())
    return inventory


def _snapshot_digest(root: Path) -> str:
    digest = hashlib.sha256()
    root = root.resolve()
    for rel_path in _inventory_files(root):
        path = root / rel_path
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"L")
            digest.update(os.readlink(path).encode("utf-8"))
        else:
            digest.update(b"F")
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=True, ignore=shutil.ignore_patterns(".git"))


def _ensure_destination_outside_source_tree(source: Path, destination: Path) -> None:
    resolved_source = source.resolve()
    resolved_destination = destination.resolve()
    if resolved_destination == resolved_source or resolved_destination.is_relative_to(resolved_source):
        raise ValueError(
            f"artifacts root cannot be inside the local source tree: {resolved_destination}"
        )


def _validate_symlinks_within_tree(root: Path) -> None:
    root = root.resolve()
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        target = (path.parent / os.readlink(path)).resolve(strict=False)
        if not target.is_relative_to(root):
            raise ValueError(f"symlink target escapes snapshot tree: {path} -> {target} (outside {root})")


def _run_git(args: list[str], *, cwd: Path | None = None) -> str:
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


def materialize_source(manifest: ContractAuditManifest, run_root: Path) -> MaterializedSource:
    run_root = Path(run_root).resolve()
    snapshot_dir = run_root / "workspace" / "source_snapshot"
    snapshot_dir.parent.mkdir(parents=True, exist_ok=True)

    source = manifest.target.source
    if source.kind == "local":
        _ensure_destination_outside_source_tree(source.local_path, snapshot_dir)
        _copy_tree(source.local_path, snapshot_dir)
        _validate_symlinks_within_tree(snapshot_dir)
        source_inventory = _inventory_files(snapshot_dir)
        source_identifier = f"snapshot:{_snapshot_digest(snapshot_dir)[:12]}"
        return MaterializedSource(
            snapshot_dir=snapshot_dir,
            source_identifier=source_identifier,
            source_mode="local",
            source_inventory=source_inventory,
        )

    clone_dir = run_root / "workspace" / "source_clone"
    if clone_dir.exists():
        shutil.rmtree(clone_dir)
    _run_git(["clone", source.repo_url, str(clone_dir)])
    _run_git(["checkout", source.commit], cwd=clone_dir)
    resolved_head = _run_git(["rev-parse", "HEAD"], cwd=clone_dir)
    if resolved_head != source.commit:
        raise ValueError(f"resolved HEAD {resolved_head} does not match requested commit {source.commit}")

    _copy_tree(clone_dir, snapshot_dir)
    _validate_symlinks_within_tree(snapshot_dir)
    source_inventory = _inventory_files(snapshot_dir)
    return MaterializedSource(
        snapshot_dir=snapshot_dir,
        source_identifier=resolved_head,
        source_mode="github",
        source_inventory=source_inventory,
    )
