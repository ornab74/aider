---
name: repair-orchestration
description: Run bounded search, edit, test, repair, verification, checkpoint, and escalation loops.
tags: [repair, tests, checkpoints, escalation, budgets]
---
# Workflow
1. Build bounded context and failure packets.
2. Make one narrow edit.
3. Run counterfactually selected tests.
4. Localize new failures and retry only while evidence changes.
5. Checkpoint on repeated failures, exhausted budgets, or high-risk verification boundaries.

# Safety
Do not continue an identical failing loop. Escalate repeated failure fingerprints, require quorum for risky patches, and preserve a resumable checkpoint before changing strategy.
