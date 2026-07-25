# Adaptive Context Surgery for Aider

This experimental layer is designed for local and small-context models that become unreliable when Aider sends large repository maps, entire source files, or broad Git diffs. It adds a search-first workflow without replacing Aider's existing coder implementations.

## What ships in this change

- **Floating context manager**: scores small overlapping source slices against the active task, touched files, pinned files, symbols, and changed paths.
- **Strict token envelope**: reserves model output/tool space before selecting context.
- **Large-file detector**: identifies files by bytes, lines, or estimated tokens.
- **Search-first windows**: turns a natural-language task into `rg` terms, extracts bounded line windows, and merges overlapping hits.
- **Verified surgical edits**: records a SHA-256 precondition, previews a unified diff, and performs an atomic replacement only when explicitly enabled.
- **SED recipe generation**: emits a backup + hash verification + bounded `sed` command for environments where SED is the preferred editor.
- **Skill discovery**: recursively finds `SKILL.md`, ranks skills, and summarizes only the sections needed for the current task.
- **Skill modifiers**: accepts `@skill:python` and `[skill:git]` hints.
- **Bracket actions**: parses `[action:terminal]...[/action]` requests.
- **Gated sandbox policy**: distinguishes read-only, workspace-write, network, destructive, and secret-access actions.

## Install and run

After installing the edited checkout:

```bash
aider-innovate context "fix retry handling" aider/coders/base_coder.py aider/commands.py

aider-innovate large-file aider/coders/base_coder.py "send_message context exhaustion"

aider-innovate skill "use pytest and monkeypatch" --root skills

aider-innovate action '[action:terminal cwd="."]rg -n "send_message" aider[/action]'
```

The command is also available without installation:

```bash
python -m aider.innovation_cli --help
```

## Proposed Aider flow

1. Detect whether a requested file is large.
2. Build a search plan instead of adding the whole file to chat.
3. Retrieve 1-8 bounded windows around matching symbols and terms.
4. Load the most relevant `SKILL.md` summary.
5. Ask the model for a bounded edit plan using line ranges and anchors.
6. Verify the file hash has not changed.
7. Preview the patch.
8. Pass any command through the action gate.
9. Apply only approved edits.
10. Refresh only the changed windows, not the full repository context.

## Twenty follow-on innovations

1. **Context leases**: every slice expires after a configurable number of turns unless reused.
2. **Entropy-based diversity**: penalize context packets dominated by one file or one symbol family.
3. **Symbol call-chain packets**: include callers and callees without including entire modules.
4. **Diff inversion prompts**: show the model the smallest reversible transformation rather than the whole diff.
5. **Anchor fingerprints**: combine line anchors with normalized AST fingerprints to survive harmless formatting changes.
6. **Context provenance ledger**: record why every line entered the prompt and whether it affected the final patch.
7. **Failure-localized retries**: on test failure, add only the failing assertion, stack frame, and nearby source window.
8. **Skill hot swapping**: load a different skill summary when the task changes from Python to Git or PowerShell.
9. **Tool budget scheduler**: reserve separate token and time budgets for search, edit, test, and repair phases.
10. **Risk-aware model routing**: send read-only exploration to a small model and destructive planning to a stronger verifier.
11. **Patch quorum**: require two independently generated edit plans to agree on high-risk files.
12. **Semantic SED**: translate symbol selections into verified line ranges before generating a SED recipe.
13. **Large-diff folding**: collapse unchanged hunks and repeated generated-code edits into one statistical summary.
14. **Repository heat map**: track frequently touched files and likely dependency neighborhoods.
15. **Context capsules**: persist compact task packets that can be resumed without replaying the full chat.
16. **Counterfactual test selection**: predict which tests would distinguish the proposed change from no change.
17. **Edit blast-radius score**: estimate downstream modules, APIs, and tests affected before applying a patch.
18. **Sandbox capability tokens**: grant a command one-time permission for a specific path and operation.
19. **MCP schema slimming**: expose only the fields and tool methods relevant to the active task.
20. **Automatic skill authoring**: turn successful repeated workflows into draft `SKILL.md` files for review.

## Security boundary

`GatedSandbox` is a policy and process gate. It does not claim kernel-level isolation. Use its generated Docker command, a disposable VM, or an external sandbox when evaluating untrusted models or packages. Network, destructive, and secret-access actions remain blocked by default.

## Wave two: durable context and risk controls

The second implementation wave completes seven of the original follow-on ideas:

- **Context leases:** each selected slice receives a bounded lifetime. Used slices renew;
  unused slices expire automatically, preventing stale context from accumulating.
- **Context provenance ledger:** every selection and use records its file, range, query,
  reasons, and content fingerprint.
- **Resumable context capsules:** packets, pinned state, leases, and provenance can be
  serialized, restored, and verified against the current workspace.
- **AST anchor fingerprints:** Python symbols are fingerprinted from their syntax tree,
  so formatting and line movement do not invalidate an edit anchor.
- **Stage budgets:** search, edit, test, and repair receive separate token and time
  allowances. Higher-risk patches automatically reserve more verification capacity.
- **Blast-radius scoring and patch quorum:** unified diffs are scored for changed volume,
  critical paths, dependency manifests, public APIs, and missing tests. High-risk edits
  require two matching patch votes; critical edits require three.
- **One-time capability tokens:** write or network approvals can be bound to one exact
  action. Tokens expire, cannot authorize another command, and are rejected on replay.

### Additional commands

```bash
# Save a resumable packet while building context.
aider-innovate context "fix retry handling" aider/coders/base_coder.py \
  --capsule-out retry-context.json

# Verify that every saved slice still matches the workspace.
aider-innovate verify-capsule retry-context.json --root .

# Produce a formatting-resilient symbol fingerprint.
aider-innovate anchor aider/coders/base_coder.py Coder.send_message

# Score a patch before allowing the model to apply it.
aider-innovate risk proposed.patch

# Allocate search/edit/test/repair resources for a risky change.
aider-innovate budget --tokens 8000 --seconds 180 --risk-score 72
```

### Remaining research backlog

The strongest next candidates are symbol call-chain packets, failure-localized retries,
risk-aware model routing, semantic SED from verified anchors, large-diff folding,
dependency heat maps, counterfactual test selection, MCP schema slimming, and automatic
skill authoring from repeated successful workflows.

## Wave three: graph-guided repair and tool-context compression

The third implementation wave turns the bounded-context layer into a tighter repair loop:

- **Symbol call-chain packets:** Python functions and methods are indexed with their callers
  and callees. A query selects seed symbols, then expands only a bounded dependency radius.
- **Dependency heat maps:** symbols are ranked by query relevance, caller/callee centrality,
  and recent file touches so the model can inspect likely blast-radius neighborhoods first.
- **Failure-localized retries:** pytest output is reduced to the failing test, assertion,
  traceback frames, and small source windows around those frames. Unrelated files are omitted.
- **Counterfactual test selection:** tests are ranked by changed-module imports, changed-symbol
  references, paired filenames, path affinity, and recent failure history.
- **MCP schema slimming:** only task-relevant tools and input fields are retained, while all
  required properties remain available. Examples, defaults, and unrelated methods are removed.
- **Large-diff folding and inversion:** repeated generated-code changes are summarized
  statistically, and a reverse patch can be generated to make rollback explicit.
- **Semantic SED:** an AST fingerprint resolves the current symbol range before producing a
  hash-guarded SED script, even when formatting or line positions changed.

### Wave-three commands

```bash
# Include only a bounded caller/callee neighborhood.
aider-innovate call-chain "trace retry handling" \
  aider/coders/base_coder.py aider/commands.py --depth 2 --heat

# Turn pytest output into a compact repair packet.
aider-innovate failure pytest-output.txt \
  aider/coders/base_coder.py tests/basic/test_coder.py

# Select the tests most likely to distinguish a proposed change.
aider-innovate select-tests tests/basic/test_coder.py tests/basic/test_commands.py \
  --changed aider/coders/base_coder.py --symbol Coder.send_message

# Remove unrelated tools and fields from a large MCP schema.
aider-innovate slim-mcp mcp-schema.json "search repository and fetch one source file"

# Summarize repeated hunks and optionally print the rollback patch.
aider-innovate fold-diff proposed.patch --invert

# Resolve a symbol and emit a hash-verified SED replacement plan.
aider-innovate semantic-sed aider/coders/base_coder.py \
  Coder.send_message replacement.py
```

### Completed backlog items

Waves two and three now implement 15 of the 20 original follow-on concepts: context
leases, call-chain packets, anchor fingerprints, provenance, failure-localized retries,
stage budgets, patch quorum, semantic SED, large-diff folding, dependency heat maps,
context capsules, counterfactual test selection, blast-radius scoring, capability tokens,
and MCP schema slimming. The remaining research areas are entropy-based context diversity,
diff inversion prompts as a model interaction format, skill hot swapping, risk-aware model
routing, and automatic skill authoring from repeated successful workflows.

## Wave four: adaptive execution and self-improving workflows

The fourth wave completes the remaining original backlog and adds a resumable control plane:

- **Entropy-based context diversity:** a maximal-marginal-relevance reranker balances
  relevance against lexical overlap, repeated files, and repeated line ranges. It reports
  normalized file entropy and covered query terms.
- **Risk-aware model routing:** provider-neutral model profiles are selected by stage,
  context/output capacity, privacy, tool reliability, retry history, and blast-radius risk.
  High-risk or repeatedly failing work receives an independent verifier when available.
- **Skill hot swapping:** file suffixes, code fences, and query terms identify the active
  language. A hysteresis policy avoids skill thrashing while still switching quickly when a
  task moves between Python, Dart, JavaScript, PowerShell, HTML, or shell work.
- **Automatic draft skill authoring:** repeated successful workflow records are clustered by
  language and converted into reviewable `SKILL.md` drafts. Writing is preview-only unless
  explicitly enabled, and generated skills retain validation and safety sections.
- **Content-addressed context cache:** packets are keyed by normalized queries and exact file
  digests, restored with immutable dataclass shapes, invalidated by path, and persisted as a
  bounded least-recently-used cache.
- **Resumable repair orchestration:** a state machine advances through search, edit, test,
  repair, verification, escalation, pause, and completion. Repeated failure fingerprints,
  exhausted budgets, and high-risk verification boundaries trigger checkpoints instead of
  endless repair loops.

### Wave-four commands

```bash
# Penalize repetitive slices and report file diversity.
aider-innovate diversify-context "fix retry auth" \
  aider/auth.py aider/transport.py --max-tokens 2400

# Route a stage using default or JSON-supplied provider-neutral model profiles.
aider-innovate route-model --stage repair --risk-score 72 \
  --context-tokens 3200 --requires-tools --retries 2

# Detect a task-language transition and select the strongest matching skill.
aider-innovate skill-swap "repair Flutter widget" lib/main.dart --root skills

# Turn repeated successful workflow history into a reviewable skill draft.
aider-innovate draft-skill workflow-history.json parser-repair python

# Compute the exact cache key for a query and workspace snapshot.
aider-innovate cache-key "fix parser" aider/parser.py tests/test_parser.py

# Advance and optionally persist a bounded repair session.
aider-innovate repair-plan repair-session.json start \
  --task "fix parser retries" --risk-score 35 --save
```

### Original backlog status

All 20 original follow-on concepts now have concrete implementations. Diff inversion is
available both as a reversible patch and as a folded summary suitable for model prompts.
Future work can move from isolated experiments toward deeper integration with Aider's normal
coder loop, provider metadata, test runner, and interactive approval UI.

## Wave five: verification intelligence and causal repair

The fifth wave moves verification earlier in the workflow, before a speculative patch is
allowed to touch the workspace:

- **Invariant contracts:** public Python symbols, function signatures, syntax validity, and
  explicit `# invariant:` declarations are extracted from the baseline and checked against
  candidate files.
- **Speculative patch arena:** multiple complete candidate patches are scored without being
  applied. Contract failures, syntax failures, blast radius, changed-line volume, and selected
  verification tests determine the winner.
- **Mutation-guided verification:** changed Python functions are scanned for comparison,
  boolean, and boundary mutations. The planner pairs those targets with the tests most likely
  to kill them, but never executes a mutation automatically.
- **Causal failure graph:** traceback frames, recently changed files, failure terms, and call
  graph connectivity are combined into ranked source-level cause hypotheses.
- **Repair trajectory memory:** long repair loops are compressed into stable phase/action
  summaries. Repeated failure fingerprints are retained as loop evidence, while successful
  trajectories receive a recall bonus for similar tasks.
- **Context-pressure telemetry:** saturation, lexical redundancy, slice churn, and cache reuse
  produce a pressure score with bounded actions such as diversifying, expiring leases,
  checkpointing facts, or routing to a larger-context model.

### Wave-five commands

```bash
# Validate a candidate file map against baseline API and invariant contracts.
aider-innovate check-contracts contract-spec.json

# Compare several complete candidate patches without applying them.
aider-innovate patch-arena patch-arena-spec.json

# Generate mutation targets and tests that should detect them.
aider-innovate mutation-plan aider/auth.py \
  --changed aider/auth.py --symbol retry_auth --test tests/test_auth.py

# Rank likely causes from pytest output, recent changes, and the call graph.
aider-innovate causal-failure pytest-output.txt aider/auth.py aider/service.py \
  --changed aider/auth.py

# Measure context saturation, redundancy, and churn recommendations.
aider-innovate context-pressure "fix retry auth" aider/auth.py aider/service.py

# Record or recall compressed repair trajectories.
aider-innovate trajectory repair-memory.json recall "auth retry failure"
```

### Verification boundary

The arena and mutation planner are intentionally planning and scoring systems. They do not
execute untrusted candidate code or mutations. Candidate execution still belongs in a
hardened disposable sandbox with the existing action policy, capability tokens, selected
tests, invariant checks, and patch quorum.
