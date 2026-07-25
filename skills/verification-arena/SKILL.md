---
name: verification-arena
description: Compare speculative code patches before applying workspace writes.
tags: [verification, contracts, mutation, patch, tests]
---
# Workflow
Extract baseline invariant contracts. Build two or more complete candidate file maps. Reject syntax or blocking contract failures. Rank the remaining candidates by blast radius, changed-line volume, and targeted-test coverage.

# Safety
Do not execute candidate code during ranking. Apply only the selected candidate after explicit write approval, then run it inside a hardened disposable sandbox.

# Validation
Run contract checks, selected tests, mutation-guided verification, and independent patch quorum for high-risk changes.
