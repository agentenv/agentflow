from __future__ import annotations

import os
from pathlib import Path


def agentflow_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def owned_skill_package_roots() -> tuple[Path, ...]:
    override = os.getenv("AGENTFLOW_OWNED_SKILLS_ROOT", "").strip()
    if override:
        return (Path(override).expanduser().resolve(),)
    return (agentflow_project_root() / ".agents" / "skills",)


def target_repo_root(repo_root: Path | None) -> Path | None:
    if repo_root is None:
        return None

    current = Path(repo_root).expanduser().resolve()
    if current.is_file():
        current = current.parent

    for base in (current, *current.parents):
        if (base / ".git").exists():
            return base
    return current


def target_repo_skill_package_roots(repo_root: Path | None) -> tuple[Path, ...]:
    resolved_repo_root = target_repo_root(repo_root)
    if resolved_repo_root is None:
        return ()

    candidate = resolved_repo_root / ".agents" / "skills"
    if not candidate.exists():
        return ()
    return (candidate,)


def vendored_skill_packages_root() -> Path:
    candidate = Path(__file__).resolve().parent / "security_skills"
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(
        f"Vendored security skills directory not found: {candidate}. "
        "Vendored skill references are disabled by default; provide an explicit vendored_root to enable them."
    )
