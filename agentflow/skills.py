from __future__ import annotations

from pathlib import Path

from agentflow.specs import TargetSkillPolicyMode
from agentflow.skill_packages import compile_rich_skill_prelude, is_package_skill_ref


def candidate_skill_paths(working_dir: Path, item: str) -> list[Path]:
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


def resolve_local_skill_path(working_dir: Path, item: str) -> Path | None:
    for candidate in candidate_skill_paths(working_dir, item):
        if candidate.is_file():
            return candidate
    return None


def compile_skill_prelude(
    skills: list[str],
    working_dir: Path,
    package_roots: tuple[Path, ...] | None = None,
    target_skill_policy: TargetSkillPolicyMode | str = TargetSkillPolicyMode.NONE,
) -> str:
    if not skills:
        return ""
    sections: list[str] = []
    unresolved: list[str] = []
    for item in skills:
        found = resolve_local_skill_path(working_dir, item)
        if found is None:
            if is_package_skill_ref(item):
                rich = compile_rich_skill_prelude(
                    [item],
                    repo_root=working_dir,
                    package_roots=package_roots,
                    target_skill_policy=target_skill_policy,
                )
                if rich:
                    sections.append(rich)
                    continue
            unresolved.append(item)
            continue
        sections.append(f"Skill `{item}` from {found}:\n{found.read_text(encoding='utf-8').strip()}")
    if unresolved:
        sections.append("Named skills without local payloads: " + ", ".join(unresolved))
    return "\n\n".join(sections)
