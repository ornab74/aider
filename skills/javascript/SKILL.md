---
name: javascript
description: Develop JavaScript and TypeScript with focused tests and safe dependency handling.
tags: [javascript, typescript, node, npm]
---
# Workflow
Inspect `package.json` and lockfiles, locate the affected module, run focused tests or type checks, and keep the patch bounded.

# Quality
Prefer explicit data flow, abortable async work, input validation, and no implicit globals.

# Safety
Do not run unknown lifecycle scripts or install packages without reviewing provenance and lockfile changes.
