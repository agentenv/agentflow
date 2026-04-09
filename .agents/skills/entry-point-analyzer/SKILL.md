---
name: entry-point-analyzer
description: Use when starting a Solidity audit and you need to enumerate external and public entry points, privileged call chains, and state-changing surfaces before deeper analysis.
---

# Entry Point Analyzer

Focus on Solidity and EVM entry points exposed by contracts in scope.
Enumerate all external and public functions, then classify state-changing entry points that can mutate storage or move value.
Document access control guards, upgrade hooks, and privileged call chains before deeper analysis.
