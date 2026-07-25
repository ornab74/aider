---
name: change-governance
description: Promote a coding change through explicit plan, snapshot, verification, quorum, and replay gates.
tags: [governance, verification, promotion, safety]
---
# Workflow
Validate the execution plan before editing. Capture the baseline repository snapshot. Stage changes in a shadow transaction and preview the unified diff. Run contracts, targeted tests, mutation checks, replay verification, and independent review before evaluating promotion.

# Promotion rules
Do not mark a change merge-ready when the repository drifted, blocking contracts fail, tests fail, mutation evidence is insufficient, the replay chain is invalid, quorum is incomplete, verifier disagreement is unresolved, or context pressure was critical.

# Safety
A merge-ready policy result is not permission to bypass protected branches, CI, code-owner review, capability gates, or sandbox requirements.
