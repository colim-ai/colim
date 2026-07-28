# Methodology

Written for a hostile skeptic. If you want to attack these numbers, this document tells you
where the weak points are — we would rather state them than have you find them.

Every number in `census/report.md` is a SQL query over `ledger/colim.sqlite`. No figure is
transcribed by hand, estimated, or interpolated anywhere in this project. Where we do not
know something, the ledger says `UNKNOWN` and the report says so.

## What we are measuring

Lean 4 proofs are code, and the compiler checking them *is* the correctness check. A Lean
package pins its compiler version in a `lean-toolchain` file. When Lean and Mathlib release
new versions — which they do roughly monthly, with intentional breaking changes — downstream
packages break.

**K** — packages in Lean's official registry (Reservoir) in scope
**M** — packages that are red on the evaluation toolchain by the regression definition below
**N** — packages we repaired to a full green build, statement-preserving
**P** — maintainer-merged PRs (week 2; nothing in this repo produces P)

## Data source

[Reservoir](https://reservoir.lean-lang.org) is Lean's official package registry. Its index is
a public git repository, [`leanprover/reservoir-index`](https://github.com/leanprover/reservoir-index),
which records for every indexed package a full **build history**: 116,874 build entries across
790 packages, from 2023-12-12 onward, each with a toolchain, a revision, an outcome, and a
**public GitHub Actions log URL**.

We work from a **pinned snapshot**, commit `2acc551043c37488cf1238059b9c657304e4e16e`
(committed 2026-07-26T15:41:49Z), recorded in `config.toml` and in `ledger.meta`. Re-cloning
that commit reproduces every input. The index does not carry `archived` or a true last-commit
date, so those come from the GitHub GraphQL API and are cached, timestamped, in
`data/raw/github_repo_meta.json`.

## The single most important caveat: Reservoir forces the toolchain

**Reservoir does not build a package on the toolchain that package pins.** It re-runs the
registry against a *given* toolchain and records what happened.

We verified this empirically rather than assuming it: Mathlib's latest indexed revision pins
`v4.33.0-rc1`, yet its `builds.json` contains three `v4.32.1` entries, all red. If Reservoir
honoured each repo's own pin, no such row could exist.

So when this project says a package is **red**, the claim is precisely:

> The package fails to build when **forced onto `leanprover/lean4:v4.32.1`**.

It is **not** the claim that the maintainer's own pinned build is broken. Most of these
packages build fine today on the older toolchain they pin. The question we are asking — and
the one the product answers — is *"can this package move to the current compiler?"*, and for
500 of 758 packages the answer is no.

If you think the weaker reading ("66% of Lean packages are broken") is the interesting claim,
we agree it would be more dramatic and we are not making it.

## Evaluation toolchain: `leanprover/lean4:v4.32.1`

Chosen on Day 1 by a stated rule, recorded in `ledger.meta`, never changed silently: *the most
recent stable (non-rc) Lean release with a recorded build outcome for ≥80% of indexed packages.*

`v4.32.1` has **98.9%** coverage. Coverage counts *recorded outcomes only* — attempted but
unrecorded is indistinguishable from unattempted, so it does not count. `census/coverage.py`
prints the full table; the next stable candidate, `v4.31.0`, sits at 95.7%.

## Load-bearing definitions

### RED_REGRESSION (this is M)
A package is a red regression iff **both**:
1. it fails on the evaluation toolchain, **and**
2. it has at least one recorded successful build on some earlier toolchain.

Both halves are required. A package that has never built green is `NEVER_GREEN` (75 packages)
and is **excluded from the headline** — it cannot have regressed. A package with insufficient
data is `UNKNOWN`. These three are never conflated, because collapsing them is the easiest way
to inflate M and the first thing an auditor should check.

"Earlier" is evaluated by **wall-clock run time**, not by parsed version number, because
Reservoir interleaves rc and stable runs and a version sort would misorder them.

### FORCED_DOWNGRADE
Because Reservoir forces the toolchain, a package whose own pin is *newer* than the evaluation
toolchain fails for the opposite reason to a regression: its source is too new for the compiler
it was forced onto. Seven packages land here, all pinning `v4.33.0-rc1` — **including Mathlib
and Batteries**.

These get their own explicit status and are **not in M**. Counting Mathlib, the healthiest
package in the ecosystem, as a "red regression" would be indefensible. We mention it because
a naive implementation of the regression rule does exactly that, and ours briefly did.

### `red_basis` — why we believe each red is red

Not every red is evidenced the same way, and the differences govern how a row may be quoted.
Every red carries a `red_basis`:

| basis | meaning |
|---|---|
| `code_error` | the log shows Lean or Lake errors in real code |
| `infra_release` | a pinned release artifact no longer downloads — a genuine, persistent dependency-acquisition failure, which is dependency rot in its most literal form. Origin is `dependency`. |
| `infra_uninformative` | the only failure was a build-cache fetch. Reservoir's log is silent on whether the code survives. |
| `measured_local` | we rebuilt it on our own machine and saw the outcome ourselves |
| `measured_actions` | we rebuilt it in our own GitHub Actions run; the log is public |

**The `infra_uninformative` cohort must be disclosed wherever M is quoted.** These packages
are red in Reservoir's recorded data, so they satisfy the RED_REGRESSION definition and remain
in M — but its runner failed before compiling anything, so the log does not show their code
failing on the evaluation toolchain.

We do not resolve this by rule, because both available rules are guesses: excluding them
assumes they are healthy, and counting them silently assumes they are broken. **We resolve it
by measurement.** Each is rebuilt in *our own repository* via a GitHub Actions matrix —
upstream cloned read-only at the exact revision Reservoir built, evaluation toolchain forced,
Mathlib cache fetched, `lake build` — with the public run log linked per package in the
ledger. Results reclassify the row mechanically:

- real code errors → stays in M, with its error classes and `measured_actions`
- builds green → **leaves M**, and the delta is logged in `census/NOTES.md`
- infrastructure fails again → stays flagged, still unmeasured

No forks are created for measurement and nothing is sent upstream; the workflow runs entirely
in our own repository (`.github/workflows/measure-reds.yml`).

Until that completes, **M is an upper bound**, and any externally quoted M states the
`infra_uninformative` count alongside it.

### A green build must actually compile something

`lake build` exits 0 having compiled **nothing** when a package configures no default target —
it only warns. `Paper-Proof/paperproof` looks green that way in 63 seconds; building its
declared libraries runs 918 jobs and fails outright inside its dependencies.

The harness therefore treats "exit 0 with no compiled target" as **inconclusive**, never as
green, and retries with the libraries and executables named in the lakefile. This matters far
beyond one row: the entire correctness argument of this project is that the Lean kernel checks
the proofs, and a build that ran no kernel checks proves nothing. No package may enter N on a
build that compiled nothing.

### Scope: what is in K
K is all Reservoir-indexed packages, minus two exclusion classes reported beside each other:

| class | n | rule |
|---|---|---|
| `ARCHIVED` | 24 | upstream marked the repo archived |
| `VANISHED` | 8 | source no longer resolves on GitHub (deleted/private/transferred) |

Everything else stays in. In particular:

- **Missing `lean-toolchain`** stays in K, and is `UNKNOWN` if unclassifiable — never red.
- **Non-Mathlib-dependent packages** stay in K. The census is the whole-market number. The
  Mathlib-downstream campaign is reported against its own denominator **K_m = 489**, never
  silently swapped for K.
- **Staleness never excludes.** Abandoned-but-once-green *is* the market. We record
  `last_commit` and publish the "active in the last 12 months" slice (K=564, M=356, 63.1%)
  because you were going to ask for it.

### Statement-preserving repair (the honesty gate for N)
A repair may change proof bodies, tactic scripts, `import` lines, and lakefile/manifest config
**only**. The set of declaration names and their types must be identical before and after.

We compare **canonical forms, never prose**: each declaration's type is serialised to a
canonical S-expression with de Bruijn-indexed binders, fully-qualified constant names, and
universe parameters by position. A pretty-printed form is emitted for humans and is never used
for comparison. Before diffing, the tier-1 rename map is applied to the old dump's constant
names, since a deprecation rename inside a downstream statement is semantically neutral.

- Canonical forms equal → auto-pass.
- Anything else → **human review bucket**, presented side by side. Never an auto-pass.

Additionally: no new `sorry` (detected via `sorryAx` in axiom dependencies), and no axioms
beyond the standard three (`propext`, `Classical.choice`, `Quot.sound`).

Genuine statement changes — where upstream semantics actually moved — are a legitimate but
**separate** flagged bucket. They are never counted in N.

Known honest gap: cross-version definitional changes can make identical-meaning types differ
structurally. Those land in the human bucket rather than passing or failing silently. N is
dozens of packages, so hand-reviewing every flagged diff is hours of work, and it makes N more
defensible rather than less.

### Counting N
A package counts in N iff its **full dependency closure resolves to public code** — either
upstream's own fix, or our fixed fork via a recorded `dep_fork_override` — **and** every fork
in that chain passed the guard itself. We never count a package whose chain includes a fork
that failed or skipped the guard. N is reported split as **N_self** vs **N_dep_assisted**, and
additionally as **N_tier1** vs **N_tier2**.

## What we have not done

- **We have not rebuilt all 500 reds ourselves.** The census runs on Reservoir's
  recorded outcomes. Day 2 reproduces a sample locally to check Reservoir against reality, and
  every package we actually repair is rebuilt from scratch by definition.
- **`failure_origin` (self / dependency / both) is resolved from build logs.** Until that pass
  completes, the split is `unknown` and is not claimed. Where it is partially resolved, it is
  reported against the resolved subset and never extrapolated.
- **The 8 vanished packages can never enter N** — there is no repo to fork.

## Reproducing this

```sh
git clone https://github.com/leanprover/reservoir-index data/raw/reservoir-index
git -C data/raw/reservoir-index checkout 2acc551043c37488cf1238059b9c657304e4e16e
python3 census/github_meta.py     # or use the cached data/raw/github_repo_meta.json
python3 census/classify.py        # rebuilds ledger/colim.sqlite
python3 census/report.py --write  # regenerates census/report.md
python3 census/test_reservoir.py  # parser regression tests
```

`census/classify.py` refuses to run if the snapshot on disk differs from the commit pinned in
`config.toml`, because the pin is load-bearing for every number above.

## Ethics

Week 1 is silent. All work happens on our own forks under a dedicated org, on branches named
`colim/bump-<toolchain>`. **No pull requests, issues, comments, or messages are sent to any
upstream repository or maintainer by this automation** — `config.toml` carries an
`allow_upstream_prs = false` tripwire that `config.assert_silent_week()` enforces. Outreach is
a week-2 decision made by humans.

Reporting tone is ecosystem health, not a wall of shame. We do not rank maintainers. Registry
inclusion requires an OSI-approved license, and forks carry upstream LICENSE files unchanged.
