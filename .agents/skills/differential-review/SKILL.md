---
name: differential-review
description: Use when reviewing Solidity diffs and you need to assess whether recent changes introduce new trust, state, value-transfer, or storage compatibility risks.
---

# Differential Review

Audit Solidity diffs with focus on newly introduced trust, state, and value transfer assumptions.
Evaluate whether changes alter invariants, access control, storage slot compatibility, or external integration behavior.
Require evidence for each changed code path instead of relying on prior audit status.
