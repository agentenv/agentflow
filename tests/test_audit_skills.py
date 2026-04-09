from __future__ import annotations

from pathlib import Path

from agentflow.skills import compile_skill_prelude


def test_compile_skill_prelude_loads_agentflow_owned_audit_defaults() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    owned_package_roots = (repo_root / ".agents" / "skills",)

    prelude = compile_skill_prelude(
        [
            "entry-point-analyzer::default",
            "foundry-solidity::default",
            "static-analysis::default",
        ],
        repo_root,
        package_roots=owned_package_roots,
    )

    assert "Skill package `entry-point-analyzer::default`" in prelude
    assert "Skill package `foundry-solidity::default`" in prelude
    assert "Skill package `static-analysis::default`" in prelude
    assert "state-changing entry points" in prelude
    assert "forge test" in prelude
    assert "semgrep" in prelude
