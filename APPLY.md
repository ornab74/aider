# Apply this bundle

Copy the files in this archive over the root of an `ornab74/aider` checkout. The only
pre-existing project file replaced is `pyproject.toml`, where this console entry is added:

```toml
aider-innovate = "aider.innovation_cli:main"
```

Run the full focused regression suite:

```bash
PYTHONPATH=. python -m pytest -q tests/basic
```

Then inspect the experimental commands:

```bash
PYTHONPATH=. python -m aider.innovation_cli --help
```

Wave four adds `diversify-context`, `route-model`, `skill-swap`, `draft-skill`,
`cache-key`, and `repair-plan`.

The action gate, capability-token system, model router, and repair orchestrator are
process-level controls. Use a hardened container or disposable VM when executing untrusted
model output or packages.
