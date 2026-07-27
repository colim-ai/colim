# Build harness — empirically verified behaviour

Every claim here was read off a real Reservoir job log or a real local run before the
corresponding code was written, per the CLAUDE.md rule.

## Reservoir's testbed procedure (what we must mirror)

Read from job `89804750687` (`0art0/kimina`, `v4.32.1`, red). Reservoir runs
`scripts/testbed-analyze.py` with a JSON matrix entry per package, and the transcript shows:

```
git clone <gitUrl> .
git fetch --tags --force
lake reservoir-config 1.0.0     # installs the repo's OWN toolchain (v4.18.0 here)
git reset --hard
git clean -ffdx
lake --version                  # now reports 4.32.1 -- the FORCED toolchain
lake build
```

Two things this settles:

1. **The toolchain really is forced.** The repo pins `v4.18.0`; Reservoir installs that pin
   while reading the package config, then builds with `v4.32.1` anyway. The log even prints
   `INFO: Building package revision 25e29d1 on leanprover/lean4:v4.18.0` while invoking
   `/home/runner/.elan/toolchains/leanprover--lean4---v4.32.1/bin/lean`. That INFO line
   reports the *declared* toolchain, not the one used — do not parse it as the build toolchain.
2. **Builds are at default-branch HEAD**, with the exact revision recorded in `builds.json`.
   We pin that revision so our reproduction is comparable to Reservoir's row.

We reproduce the forcing with `ELAN_TOOLCHAIN=<eval toolchain>`, which elan honours in
preference to `lean-toolchain`.

**Validated end to end**: `0art0/kimina` reproduces locally with byte-identical error lines
(`Kimina/Config.lean:33:2: \`group\` is not a field of structure \`Option.Decl\``), in 4s.

## Lake 5.0.0 (Lean 4.32.1) surprises

- **There is no jobs flag.** Neither `-j` nor `--jobs` exists; `lake build -j 2` fails with
  `unknown short option '-j'`. Lake already parallelises to the core count, which on this
  2-core box *is* the "lake jobs = cores" setting we want. `build.lake_jobs` stays in config
  for the day the flag returns, and `build_package` asserts on it so the two cannot silently
  diverge.
- **`--packages=file`** — "JSON file of package entries that override the manifest". This is
  exactly the mechanism Day 6 needs for `dep_fork_override`: pointing a package at our fixed
  fork without editing its lakefile. Preferred over rewriting manifests by hand.
- **`--no-cache` / `--try-cache`** exist as first-class Lake options; Lake has its own build
  cache separate from Mathlib's `lake exe cache get`. The Reservoir error string
  `error: mathlib: failed to fetch cache` comes from Lake's cache layer, not from Mathlib's
  cache exe.

## Log format

Actions logs prefix every line with an ISO-8601 timestamp and a space; `strip_timestamps()`
removes it. Logs run ~3,000 lines / ~120 KB each and are cached gzipped under
`data/raw/logs/<job_id>.log.gz` (~11 KB compressed).

Errors worth parsing take the form `error: <path>.lean:<line>:<col>: <message>`. Lake also
emits bare `error: build failed` and `error: Lean exited with code 1`, which carry no
attribution and must not be treated as the first real error.

## Infrastructure failures are a real false-positive class

Some recorded reds are not code breakage at all — the runner failed to obtain a toolchain or
a cache:

- `error: mathlib: failed to fetch cache` (seen on `teorth/Analysis`, `ImperialCollegeLondon/FLT`)
- `error: could not download file from 'https://releases.lean-lang.org/lean4/...'` (FLT)
- `error: failed to extract package`

These are classified `infra_*` in `errors.py` and reported separately. A package whose only
failure is infrastructural tells us nothing about whether its code survives the toolchain, so
folding it into M would overstate the number.

## failure_origin attribution

Reservoir reports dependency errors with paths relative to the **dependency root**
(`Batteries/Data/Array/Match.lean`), not under `.lake/packages/`. So attribution matches the
first path segment against the package's declared dependency names from `versions.json`, and
also handles the `.lake/packages/<dep>/` form when it appears. Dependency-resolution failures
(`revision not found`, clone failures) are dependency-origin by construction — the build never
reached compilation.
