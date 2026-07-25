---
name: model-routing
description: Route bounded coding stages to model profiles using risk, context, tools, privacy, and retry signals.
tags: [models, routing, risk, verification, local]
---
# Workflow
1. Estimate input and output tokens before selecting a profile.
2. Use fast local profiles for low-risk search and summarization.
3. Increase quality and tool-reliability weighting for edits, repairs, and retries.
4. Attach an independent verifier for high-risk or repeatedly failing patches.

# Safety
Never route around privacy constraints, context limits, tool requirements, patch quorum, or action gates merely to obtain a faster answer.
