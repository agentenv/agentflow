---
name: constant-time-analysis
description: Use when reviewing cryptographic or verification logic for secret-dependent timing, gas-observable behavior, or side-channel leakage risks in EVM-related systems.
---

# Constant Time Analysis

Assess timing and gas-observable behavior where secret-dependent branching could leak sensitive information.
Focus on cryptographic helpers, signature verification, and any offchain/onchain hybrid assumptions.
Call out cases where constant-time guarantees are infeasible on EVM and recommend practical mitigations.
