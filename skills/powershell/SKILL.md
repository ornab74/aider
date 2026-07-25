---
name: powershell
description: Build safe Windows PowerShell commands and scripts.
tags: [powershell, windows, pwsh]
---
# Workflow
Use `$ErrorActionPreference = 'Stop'`, `Join-Path`, `Test-Path`, and explicit parameter names. Prefer `Get-ChildItem`, `Select-String`, and `Get-Content` for inspection.

# Safety
Use `-WhatIf` where supported. Avoid execution-policy bypasses, encoded commands, and recursive deletion without a confirmed target.
