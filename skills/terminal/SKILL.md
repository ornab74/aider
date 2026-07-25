---
name: terminal
description: Run POSIX terminal commands with read-first exploration and bounded writes.
tags: [shell, bash, linux, terminal]
---
# Workflow
Start with `pwd`, `git status -sb`, `find`, and `rg`. Quote paths, use `set -euo pipefail` in scripts, and preview destructive changes.

# Safety
Avoid `sudo`, recursive deletion, pipes into shells, and writing outside the workspace unless explicitly approved. Prefer temporary files plus atomic rename.
