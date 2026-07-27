# Reservoir index — empirically verified structure

Everything below was checked against the actual snapshot before `reservoir.py` was
written, per the CLAUDE.md rule. Findings are stated with the evidence that produced them.

**Snapshot pinned:** `leanprover/reservoir-index` @ `2acc551043c37488cf1238059b9c657304e4e16e`
(committed 2026-07-26T15:41:49Z), shallow-cloned to `data/raw/reservoir-index`, 71 MB on disk.

## Headline finding: per-package build history EXISTS

The ~75% prior in CLAUDE.md is **confirmed**. The degradation contingency (a) is not needed;
the regression/never-green split is real data, not UNKNOWN.

`builds.json` holds the full history — 116,873 build entries across 790 packages, ranging
from 2023-12-12 to 2026-07-26, over 86 distinct toolchains. Mathlib alone has 435 entries.

Each entry carries a **public GitHub Actions log URL**, which satisfies the
"one click from a public log" definition-of-done directly — no log rehosting needed.

## Layout

```
<owner>/<package>/metadata.json   # 790
<owner>/<package>/builds.json     # 790
<owner>/<package>/versions.json   # 789  (3 missing, see below)
```

- Directory names are Reservoir's *normalized* package names, which frequently differ from
  the GitHub repo name (`leanprover-community/mathlib` ← repo `mathlib4`). Never assume
  dir name == repo name; read `sources[0].fullName`.
- Some package directories **contain spaces** (`lean-ja/lean by example`,
  `seasawher/lean book`). Anything shelling out to paths must quote.
- **Alias entries are plain files, not directories**, e.g. `leanprover-community/mathlib4` is
  a blob containing `{"alias": {"from": ..., "to": ...}}`. Globbing `*/*/metadata.json`
  therefore skips them automatically and no dedup pass is required.

## Schemas

### metadata.json
`name, owner, fullName, description, keywords, homepage, license, createdAt, updatedAt,
stars, sources[], schemaVersion`. Each source has `{type, host, id, fullName, repoUrl,
gitUrl, defaultBranch}` where `id` is the **GitHub GraphQL node id** — this is what makes
`github_meta.py` able to batch 100 repos per call.

**Not present: `archived`, and no true last-commit.** `updatedAt` is the GitHub repo
`updated_at`, which moves on metadata changes (stars, description), so it is *not* a commit
date. Both `archived` and `last_commit` must come from the GitHub API — hence `github_meta.py`.

### builds.json — `{schemaVersion, data: [...]}`
Entries are stored **newest-first**; `reservoir.py` re-sorts by `runAt` anyway rather than
trusting file order.

Current shape (116,872 of 116,873 entries):
`built (bool), tested, toolchain, requiredUpdate, archiveSize, archiveHash, runAt, url, revision`

Schema drift found — handled explicitly in `_read` / `load_package`:
- **1 file is a bare list** with no `{schemaVersion, data}` envelope.
- **2 entries** (both in `katzenpost/crypt_walker`) use the pre-rename keys
  `outcome: "success"` and `builtAt` instead of `built` and `runAt`.
- 33 entries predate the `archiveHash` field.

### versions.json — `{schemaVersion, data: [...]}`
`version, revision, date, tag, toolchain, platformIndependent, license, licenseFiles,
readmeFile, dependencies[]`.

Two fields carry real weight for us:
- **`toolchain`** — the revision's own `lean-toolchain` pin. This gives `old_toolchain`
  for the whole registry with zero clones.
- **`dependencies[]`** — `{type, name, scope, version, transitive, rev, inputRev, url}`.
  This gives **`mathlib_downstream` for free**, including the `transitive` flag, so K_m
  needs no repo checkouts either.

Entries duplicate freely (same revision listed repeatedly); dedup before counting versions.

**Dependency matching is a trap — two bugs found and fixed here.** The first pass detected
only 262 mathlib-downstream packages when the true figure is ~503, because:

1. Mathlib's dependency URL is `https://github.com/leanprover-community/mathlib4**.git**` —
   a raw string comparison against the un-suffixed URL misses every single one.
2. Mathlib is recorded with **`scope: null`**, not `scope: "leanprover-community"`, so the
   scope+name fallback missed it too. Scope is populated for `plausible`, `aesop`, `batteries`
   etc. but not for mathlib itself.

Both failure modes are silent — they produce a plausible-looking smaller number. `normalize_repo()`
now reduces any GitHub URL to lowercase `owner/repo` before matching. Sanity check when
touching this: `ImperialCollegeLondon/FLT`, `teorth/Analysis` and `AlexKontorovich/PrimeNumberTheoremAnd`
must all come back True.

The dependency list is the **flattened closure** (each entry carries a `transitive` flag), so a
direct scan catches indirectly-downstream packages without a graph walk.

`depends_on_mathlib()` reads the **latest** version only. 12 packages have an empty dependency
list on their latest version but a non-empty one earlier; for 4 of them (`pandaman64/lean-regex`,
`Paper-Proof/paperproof`, `T-Brick/numbers`, `Vilin97/Clawristotle`) mathlib appears only in the
older entry. Latest-version semantics treats these as no-longer-downstream, which is the
intended reading — but an empty list can also mean Reservoir failed to resolve the manifest, so
these 4 are a known ±4 sensitivity on K_m = 495, not a certainty.

**3 packages have no versions.json**: `katzenpost/crypt_walker`, `seasawher/lean book`,
`lean-ja/lean by example`. They get an empty version list, so `pinned_toolchain` and
`depends_on_mathlib` are `None` → UNKNOWN, never RED. This is the CLAUDE.md rule for
missing `lean-toolchain` applied one level up.

## Load-bearing semantic finding: Reservoir FORCES the toolchain

This one changes how every red row must be read, so it is stated plainly.

Reservoir does **not** build each package on the toolchain the package pins. It re-runs the
registry against a *given* toolchain and records the outcome. Evidence: mathlib's latest
indexed revision pins `v4.33.0-rc1`, yet `builds.json` contains three `v4.32.1` entries, all
red. If Reservoir honoured the repo's own pin, no such row could exist.

Consequence: "red on v4.32.1" means **"fails when forced onto v4.32.1"**, not "the
maintainer's pinned build is broken." That is exactly the regression semantics this project
wants — it is the *"does your code survive the new compiler"* question — but METHODOLOGY.md
must say so in these words, because a hostile reader will otherwise assume the weaker claim.

The `requiredUpdate` boolean appears to flag runs where the manifest/toolchain had to be
updated to attempt the build. Not yet used; verify before relying on it.

## Multiple entries per (package, toolchain)

Reservoir retries, so a package can hold both green and red rows for the same toolchain
(mathlib on v4.31.0: 1 green, 4 red). `outcome_on()` therefore takes the **most recent**
entry by `runAt`, which makes "recorded outcome" single-valued as the coverage rule requires.

`ever_green_before()` deliberately compares by **`runAt` wall-clock, not parsed version**,
because Reservoir interleaves rc and stable runs and a version sort would misorder them.

## GitHub fetch results (`data/raw/github_repo_meta.json`, fetched 2026-07-27)

- 782 of 790 resolved.
- **24 archived** → excluded from K, reported separately per CLAUDE.md.
- **8 node ids no longer resolve** — repo deleted, made private, or transferred:
  `leanprover/leanbv`, `Xiyou-Wu/RiemannianGeometry`, `pitmonticone/NewProject`,
  `quangvdao/Zklib`, `katzenpost/crypt_walker`, `klavins/LeanBook`,
  `physicslib/Physicslib`, `jonwashburn/Riemann`.
  A stale id makes the *entire* GraphQL batch error rather than returning partial data, so
  `fetch_batch` bisects on that specific error to isolate offenders.
  **Open ruling needed** (see report): CLAUDE.md excludes *archived* from K but says nothing
  about *vanished*. Current default keeps them in K — the rule excludes archived, and we have
  no evidence these are archived — flagged as `source_unavailable` and disclosed. They can
  never enter N, since there is no repo to fork.
