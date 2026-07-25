---
name: git
description: Inspect, stage, commit, branch, merge, and recover Git work safely.
tags: [git, version-control, diff, commit]
---
# Workflow
Inspect `git status -sb`, review `git diff`, stage explicit paths, run relevant tests, then commit with a focused message.

# Safety
Do not reset, clean, force-push, or rewrite shared history without explicit approval. Preserve unrelated worktree changes.

# Useful commands
Use `git diff --stat`, `git diff -- <path>`, `git log --oneline --decorate -20`, and `git restore --staged <path>`.
