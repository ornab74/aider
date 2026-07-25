---
name: python
description: Develop and test Python code with small diffs and isolated environments.
tags: [python, pytest, typing, venv]
---
# Workflow
Identify the narrow module and tests, reproduce the failure, make the smallest edit, run focused pytest tests, then run the broader relevant suite.

# Quality
Support Python 3.10+, use type hints where they improve interfaces, keep imports deterministic, and avoid hidden global state.

# Safety
Use a virtual environment. Do not install unverified packages or execute repository scripts before inspection.
