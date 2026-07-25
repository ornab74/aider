---
name: causal-repair
description: Localize failures using trace frames, changed files, symbols, and call-graph evidence.
tags: [failure, traceback, causal, repair, graph]
---
# Workflow
Parse the failing assertion and traceback. Rank recently changed symbols that appear in frames or connect to failing callers. Build a bounded repair packet around the highest-scoring cause, then perform one repair attempt.

# Stop conditions
Checkpoint and escalate when the same failure fingerprint repeats, context pressure becomes high, or the repair token budget is exhausted.

# Validation
Use counterfactual tests and invariant contracts to confirm the repair without broad full-repository context.
