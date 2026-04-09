---
name: insecure-defaults
description: Use when reviewing Solidity constructors, initializers, or deployment settings for production-unsafe default values and fail-open configuration.
---

# Insecure Defaults

Identify defaults in Solidity contracts that are unsafe in production contexts.
Review constructor and initializer defaults for ownership, pause state, limits, and oracle sources.
Recommend secure-by-default settings that fail closed on missing configuration.
