# Adaptive Context Surgery for Aider

This opt-in layer is designed for local and small-context models that become unreliable when Aider sends large repository maps, entire source files, or broad Git diffs.

## Included

- Floating context manager with bounded overlapping slices.
- Token reserve for model output and tools.
- Large-file detection by bytes, lines, and estimated tokens.
- Search-first `rg` plans and merged line windows.
- SHA-256 guarded surgical edits with atomic writes.
- Verified SED recipe generation with backup and diff.
- Recursive `SKILL.md` discovery, ranking, summarization, and `@skill:name` modifiers.
- `[action:terminal]...[/action]` parsing.
- Read-only, write, network, destructive, and secret-access safety levels.

## Usage

```bash
aider-innovate context "fix retry handling" aider/coders/base_coder.py aider/commands.py

aider-innovate large-file aider/coders/base_coder.py "send_message context exhaustion"

aider-innovate skill "use pytest and monkeypatch" --root skills

aider-innovate action '[action:terminal cwd="."]rg -n "send_message" aider[/action]'
```

The module can also run without installing the console entry point:

```bash
python -m aider.innovation_cli --help
```

## Proposed workflow

1. Detect whether a requested file is large.
2. Build a search plan instead of adding the whole file to chat.
3. Retrieve bounded windows around matching symbols and terms.
4. Load only the relevant sections of the best skill.
5. Ask the model for a bounded edit plan using line ranges and anchors.
6. Verify the file hash has not changed.
7. Preview the patch.
8. Gate every requested tool action.
9. Apply only approved edits.
10. Refresh only changed windows.

## Twenty follow-on innovations

1. Context leases that expire unused slices.
2. Entropy-based diversity across files and symbols.
3. Symbol call-chain packets without whole modules.
4. Diff inversion prompts showing only reversible transformations.
5. AST anchor fingerprints resilient to formatting changes.
6. Context provenance ledgers.
7. Failure-localized retries using only assertions, frames, and nearby source.
8. Skill hot swapping when the task language changes.
9. Separate token/time budgets for search, edit, test, and repair.
10. Risk-aware model routing.
11. Patch quorum for high-risk files.
12. Semantic SED generated from verified symbol ranges.
13. Large-diff folding for repeated generated-code edits.
14. Repository heat maps of dependency neighborhoods.
15. Resumable context capsules.
16. Counterfactual test selection.
17. Edit blast-radius scoring.
18. One-time sandbox capability tokens.
19. MCP schema slimming.
20. Automatic draft skill authoring from repeated successful workflows.

## Wave two: durable context and risk controls

The second implementation wave completes seven of the original follow-on ideas:

- **Context leases:** selected slices receive bounded lifetimes. Used slices renew and unused slices expire, preventing stale context accumulation.
- **Context provenance:** every selection and use records its path, range, query, reasons, and content fingerprint.
- **Resumable capsules:** packets, pinned state, leases, and provenance can be serialized and verified against the current workspace.
- **AST symbol anchors:** Python symbols are fingerprinted structurally so formatting and line movement do not invalidate anchors.
- **Stage budgets:** search, edit, test, and repair receive separate token and time allowances; risky patches reserve more verification capacity.
- **Blast-radius and quorum:** diffs are scored for volume, critical paths, manifests, public APIs, and missing tests. High-risk patches require two matching votes; critical patches require three.
- **One-time capability tokens:** sensitive approvals are cryptographically bound to one exact action, expire, and fail on replay or command substitution.

The `InnovationEngine` coordinates these controls and can checkpoint a verified context capsule after analysis.

## Remaining research backlog

The strongest next candidates are symbol call-chain packets, failure-localized retries, risk-aware model routing, semantic SED from verified anchors, large-diff folding, dependency heat maps, counterfactual test selection, MCP schema slimming, and automatic skill authoring from repeated successful workflows.

## Security boundary

`GatedSandbox` is a policy and process gate, not kernel isolation. Use its generated Docker command, a disposable VM, or another hardened sandbox for untrusted models and packages. Network, destructive, and secret-access actions are blocked by default.
