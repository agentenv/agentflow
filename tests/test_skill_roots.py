from __future__ import annotations

from pathlib import Path

import pytest

from agentflow import skill_roots


def test_owned_skill_package_roots_honor_environment_override(tmp_path: Path, monkeypatch):
    custom_root = tmp_path / "owned-skill-packages"
    monkeypatch.setenv("AGENTFLOW_OWNED_SKILLS_ROOT", str(custom_root))

    assert skill_roots.owned_skill_package_roots() == (custom_root.resolve(),)


def test_vendored_skill_packages_root_raises_clear_error_when_default_directory_is_missing(
    tmp_path: Path,
    monkeypatch,
):
    fake_module = tmp_path / "agentflow" / "skill_roots.py"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("# fake module path for test\n", encoding="utf-8")
    monkeypatch.setattr(skill_roots, "__file__", str(fake_module))

    with pytest.raises(FileNotFoundError, match="Vendored security skills directory not found"):
        skill_roots.vendored_skill_packages_root()
