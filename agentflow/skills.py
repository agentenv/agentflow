from __future__ import annotations

from pathlib import Path


def _candidate_paths(working_dir: Path, item: str) -> list[Path]:
    raw = Path(item).expanduser()
    if raw.is_absolute():
        return [raw, raw.with_suffix(".md"), raw / "SKILL.md"]
    return [
        working_dir / item,
        working_dir / f"{item}.md",
        working_dir / item / "SKILL.md",
        working_dir / "skills" / item,
        working_dir / "skills" / f"{item}.md",
        working_dir / "skills" / item / "SKILL.md",
    ]


def _resolve_skill_path(working_dir: Path, item: str) -> Path | None:
    for candidate in _candidate_paths(working_dir, item):
        if candidate.is_file():
            return candidate
    return None


def compile_skill_prelude(skills: list[str], working_dir: Path) -> str:
    if not skills:
        return ""
    sections: list[str] = []
    unresolved: list[str] = []
    for item in skills:
        found = _resolve_skill_path(working_dir, item)
        if found is None:
            unresolved.append(item)
            continue
        sections.append(f"Skill `{item}` from {found}:\n{found.read_text(encoding='utf-8').strip()}")
    if unresolved:
        sections.append("Named skills without local payloads: " + ", ".join(unresolved))
    return "\n\n".join(sections)
