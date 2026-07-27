# CLAUDE.md — Colim, Week-1 Evidence Campaign

## What this project is

Colim automatically repairs formal math (Lean 4) code when it breaks — "Dependabot for math."
This repo is NOT the product. It is a one-week evidence campaign whose deliverable is four
auditable numbers for a YC application:

- **K** — packages in Lean's official registry (Reservoir) in scope
- **M** — packages red on the current toolchain by our regression definition
- **N** — packages Colim repaired (full build green, statement-preserving)
- **P** — maintainer-merged PRs (week 2; out of scope for this repo's automation)

Guiding principle: **disposable evidence machinery.** Every component is the dumbest thing
that produces auditable rows. Optimize for verifiability, not elegance or reuse. If a claim
is not derivable from a ledger row linked to a public log, we do not make the claim.

## Domain context (compressed)

- Lean 4 proofs are code; the compiler/kernel checking them IS the correctness check.
- A repo pins its compiler via a `lean-toolchain` file; `elan` reads it automatically.
- `lake` is Lean's build tool. Mathlib-dependent projects MUST run `lake exe cache get`
  before `lake build` (cache turns CPU-hours into minutes).
- Lean/Mathlib intentionally break downstream code across releases (renames, restatements,
  removed lemmas, lakefile/Lake API churn). That breakage is the market; repairing it is the demo.
- Reservoir (reservoir.lean-lang.org) is the official package index. It rebuilds indexed
  packages on new toolchain releases and publishes outcomes. Its data is our census source.
- Registry inclusion requires an OSI-approved license → forking any indexed repo is fine.

## Load-bearing definitions (never weaken these)

1. **RED_REGRESSION**: package fails to build on the evaluation toolchain AND has at least
   one successful build on some earlier toolchain. Packages that never built green are
   **NEVER_GREEN** and are excluded from headline numbers. Packages with insufficient data
   are **UNKNOWN**. Never conflate these; the headline number must survive hostile audit.
   - Every red package also gets `failure_origin ∈ {self, dependency, both, unknown}`,
     classified from the build log. Report the split — dependency rot is the product
     thesis, so we count it and disclose it rather than hiding it.
2. **Evaluation toolchain**: most recent stable (non-rc) Lean release with build coverage
   for ≥80% of indexed packages. Chosen on Day 1, recorded in `ledger.meta`, never changed
   silently.
   - **Coverage** = fraction of K with a *recorded outcome* (green or red) for that
     toolchain in Reservoir data. Attempted-but-unrecorded is indistinguishable from
     unattempted, so it does not count.
   - If no stable toolchain reaches 80%, take the newest stable that maximizes coverage and
     print the coverage % next to every headline use of the number.
3. **Statement-preserving repair** (the honesty gate): a repair may change proof bodies,
   tactic scripts, `import` lines, and lakefile/manifest config ONLY.
   - The set of declaration names and their types must be identical before/after. Compare
     **canonical forms, never prose** (see Guard ruling below); mismatches go to the
     human-review bucket, not auto-pass.
   - No new `sorry` (detect via `sorryAx` in axiom dependencies).
   - No axioms beyond the standard three: `propext`, `Classical.choice`, `Quot.sound`.
   - Genuine statement changes (upstream semantics moved) are a legitimate but SEPARATE
     flagged bucket for human review. They are never counted in N.
4. **Tier 1 fix**: deterministic — Mathlib deprecation/rename maps, identifier-boundary-aware
   rewriting (skip strings/comments), lakefile migration templates, toolchain + Mathlib pin
   bump. A bad rewrite yields a red build, never a false green (kernel is the net), so do not
   over-engineer safety here.
5. **Tier 2 fix**: LLM repair loop, run ONLY on Tier-1 leftovers. Per failing file:
   prompt = compiler error + ±40 lines context + old/new upstream declaration if known +
   prior attempts; ≤4 iterations; per-package and global dollar caps (read from config).
   System prompt forbids statement edits and instructs emitting `CANNOT_FIX` rather than
   weakening a claim. **Log every attempt** — (error, context, candidate patch, verdict) —
   success or failure. Attempt logs are a first-class deliverable (training-data flywheel).

## Census scope (K) — binding

K = **all Reservoir-indexed, non-archived packages.** Rules:

- **Archived repos** → excluded from K, counted and reported separately.
- **Missing `lean-toolchain`** → stays in K. If unclassifiable it is UNKNOWN, never RED.
- **Non-Mathlib-dependent** → stays in K. The census is the whole-market number. The Day-6
  campaign targets the Mathlib-downstream subset, reported against its own denominator
  **K_m**, never silently swapped for K.
- **Mathlib itself + release-train packages** (lean4, batteries, aesop, proofwidgets, and
  anything vendored in Mathlib's own manifest) → in K (they classify green), excluded from
  the campaign.
- **Staleness** → never exclude by last-commit age. Abandoned-but-once-green IS the market.
  Record `last_commit` so the report can show the "restricted to repos active in the last
  12 months" slice a skeptic will ask for.

## Reservoir build history — verify, then degrade gracefully

Prior (~75%): per-package build *history* exists — an array of build entries
(toolchain, revision, outcome) — because Reservoir re-runs the registry on each toolchain
release and its index is a data repo. Verify empirically before writing the parser.

If it turns out **latest-only**, do NOT expand Day 1 into building old toolchains. Instead:

- (a) Day-1 headline degrades to "X% of the registry fails on the current toolchain." The
  regression / never-green split is written as UNKNOWN in the ledger. UNKNOWN is fine; a
  guessed split is not.
- (b) **N certifies itself.** The guard's baseline signature dump requires elaborating the
  old code on its pinned toolchain, so every package we repair is a verified regression by
  construction. The airtight number survives even if M gets fuzzier.
- (c) Optionally, Day 2+ may old-toolchain-build a *random sample* of reds to estimate the
  regression fraction. Prose-labeled estimate only — never a ledger value.

## Guard ruling — normalize structurally, don't compare prose

The signature dumper serializes each declaration's type to a **canonical S-expression**:
de Bruijn-indexed binders (names erased), fully-qualified constant names, universe params
by position. It also emits a pretty-printed form, for humans only — never for comparison.

Before diffing, apply the tier-1 rename map to the OLD dump's constant names: a deprecation
rename appearing inside a downstream statement is semantically neutral.

- Canonical forms equal → auto-pass.
- Anything else → human bucket, presented side-by-side with pretty types.

Known honest gap: cross-version definitional changes can make identical-meaning types differ
structurally. The bucket catches those. Worst case is acceptable by design — N is dozens of
packages, so hand-reviewing every flagged diff is hours, and it makes N more defensible.

## Counting N — binding

A package counts in N iff its **full dependency closure resolves to public code** — either
upstream's own fix, or our fixed fork via `dep_fork_override` — AND every fork in that chain
passed the guard itself.

- Never count a package whose chain includes a fork that failed or skipped the guard.
- Report N split as **N_self** vs **N_dep_assisted**. The flagship screenshot comes from
  N_self.

## Config values (never hardcode)

- `GH_ORG` — dedicated org for forks; set by the humans, read from config. Never a personal
  account (branch URLs go in the application). Forks keep upstream names; collision
  convention is `owner--repo`.
- Tier-2: primary model `claude-sonnet-4-6`; per-package cap **$3**; global cap **$150**;
  ≤4 iterations per file. Log the model string + params on every attempt row. Escalating
  stubborn packages to a larger model is a post-Day-5 config change, not a Day-5 decision.

## Compute

- **Day-2 red reproduction: local** — fast iteration, direct log capture.
- **Green proof: public GitHub Actions on our forks.** Free on public repos; runner disk is
  ~14GB, which fits a Mathlib cache but tightly — use the standard Mathlib CI pattern.
- Local budgets: Mathlib cache is shared per-machine across projects on the same rev (one
  download, order-of-GBs); toolchains ~1–2GB each. Plan ~100GB free disk, ≤3 concurrent
  builds, ~8–16GB RAM headroom. Set concurrency from the actual box specs.
- **Full-registry local reproduction is explicitly NOT required.** The census runs on
  Reservoir's recorded outcomes; we only reproduce the sample + the campaign set.

## Repo layout

```
colim/
  census/    # Reservoir index → K, M, classification, report
  harness/   # clone @ pinned rev, elan/lake orchestration, cache, build, log capture+hash
  fixers/
    tier1/   # deprecation map extraction, mechanical rewriter, lakefile migration, bump
    tier2/   # LLM loop, prompts, budgets, attempt logging
  guard/     # Lean signature dumper (metaprogram), statement diff, sorry/axiom sweep
  campaign/  # fork/branch/push automation, injected GitHub Actions workflow template
  ledger/    # SQLite: the spine; schema + accessors; one row per package
  site/      # static dashboard generated FROM the ledger (no independent data)
  data/raw/  # cached upstream data (reservoir index, API responses), timestamped
```

## Ledger (SQLite) — the spine

- `meta`: evaluation toolchain, index snapshot commit/timestamp, run ids.
- `packages`: repo, owner, stars, license, archived, last_commit, mathlib_downstream,
  old_toolchain, target_toolchain, red_state_log_hash, error_classes, failure_origin
  {self, dependency, both, unknown}, tier1_result, tier2_attempt_count, final_status
  {GREEN_CURRENT, RED_REGRESSION, NEVER_GREEN, UNKNOWN, REPAIRED, FLAGGED_STATEMENT_CHANGE,
  CANNOT_FIX}, statement_diff_verdict, branch_url, ci_run_url, dep_fork_override (bool+note).
- `attempts`: package, file, iteration, model, prompt_hash, error_before, patch, verdict, cost.
- Every number in any report/dashboard/README must be a query over these tables.

## Engineering rules

- Python 3.11+, shell out to `git` / `gh` / `elan` / `lake`. SQLite. Static HTML site.
  No queues, no k8s, no services — a resumable for-loop with retries.
- Idempotent + resumable everywhere: rerunning any stage skips completed work
  (keyed by ledger state), never double-counts.
- Pin everything: repo revisions, index snapshot commit, toolchain versions, model names.
  Hash and store all build logs (red and green).
- Parallelism: builds are RAM-hungry; default ≤3 concurrent builds, configurable.
- GitHub API: authenticated via `gh`, sequential-ish, backoff on 403/429; fork lazily.
- Never fabricate, estimate, or interpolate a number. UNKNOWN is an acceptable value;
  a made-up value is not. Mark estimates as estimates in prose, never in the ledger.
- Verify upstream data structures empirically (inspect actual files/responses) before
  writing parsers; document findings in a NOTES.md next to the parser.

## Community & ethics rules (hard constraints)

- Week 1 is SILENT: work only on our own forks/branches. **No PRs, no issues, no comments,
  no Zulip posts, no emails to maintainers.** Outreach is week 2, by the humans.
- Fork-only workflow: fork under our org, branch `colim/bump-<toolchain>`, push, run injected
  Actions workflow so green checks are public and timestamped on OUR forks.
- Dashboard/report tone: ecosystem health, never wall-of-shame. No ranking maintainers.
- Respect licenses; carry upstream LICENSE files in forks (forking does this by default).

## Week-1 roadmap (cut from the top if a day slips, never the bottom)

Day 1 = **Monday 2026-07-27**. Day 7 = **Sunday 2026-08-02**. If days slip, the silent-week
rule travels with the work, not the calendar: no outreach until the campaign artifacts
exist, whatever the date.

- Day 1 — Census (K, M, classification, report, spot-check candidates)
- Day 2 — Build harness; reproduce red states for ~20-package sample; error classifier v0;
          founders hand-repair 2 packages (notes feed the pipeline design)
- Day 3 — Tier-1 fixer + bump script; tier-1 fix rate on sample
- Day 4 — Guard (signature dumper, statement diff, contraband sweep) — BEFORE tier 2 exists
- Day 5 — Tier-2 loop with budgets + full attempt logging
- Day 6 — Campaign across RED_REGRESSION Mathlib-downstream set, topologically ordered
          (fix upstream deps first; temporary manifest overrides to our fixed forks are
          allowed but recorded as dep_fork_override)
- Day 7 — Dashboard + METHODOLOGY.md (written for a hostile skeptic) + draft (unsent)
          Zulip post + fill M/K/N/P

**Definition of done:** M, K, N (P pending week 2), each one click from a public log.

## Current status

Day 1 (2026-07-27). Nothing built. First task: Day-1 census — starting with empirical
inspection of the Reservoir index to settle whether per-package build history exists
(see contingency above) before any parser is written.

Open human action: create `GH_ORG` (dedicated org, both founders added), then confirm the
identity Claude Code inherits with `gh auth status` on this box.
