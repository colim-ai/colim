"""Error classifier v0 — Reservoir build log -> error classes + failure origin.

Deliberately a pile of regexes over real log text. Every pattern here was read
off actual logs first (see NOTES.md); none is speculative. When a log does not
match, the answer is `unknown` -- never a guess.

Two outputs per log:

  error_classes   what broke, as a set of taxonomy labels
  failure_origin  self | dependency | both | unknown  -- WHOSE code broke

failure_origin is the product thesis: dependency rot is what Colim exists to
fix, so we count it and disclose it rather than folding it into one number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Infrastructure failures.
#
# These are NOT code regressions -- the runner failed to obtain a toolchain,
# a cache, or a git revision. Treating them as "your package is broken" would
# be wrong, so they get their own class and are reported separately.
# --------------------------------------------------------------------------
#
# NOTE ON CASE: Lean 4.3x capitalises diagnostic messages ("Unknown identifier",
# "Type mismatch", "Invalid field") where older versions did not. A first pass
# with lowercase-only patterns silently undercounted every Lean class. All Lean
# patterns are therefore case-insensitive; do not "tidy" that away.
#
INFRA_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("infra_cache_fetch", re.compile(r"failed to fetch cache", re.I)),
    ("infra_toolchain_download", re.compile(r"could not download file from '.*releases\.lean-lang", re.I)),
    ("infra_toolchain_missing", re.compile(r"no default toolchain configured", re.I)),
    ("infra_extract_failed", re.compile(r"^error: failed to extract package", re.M | re.I)),
    ("infra_release_fetch", re.compile(r"failed to fetch GitHub release", re.I)),
    ("infra_network", re.compile(r"(Connection timed out|Could not resolve host|curl: \(\d+\))", re.I)),
    ("infra_runner_disk", re.compile(r"No space left on device", re.I)),
    ("infra_timeout", re.compile(r"(The job running on runner .* has exceeded|##\[error\]The operation was canceled)", re.I)),
]

# --------------------------------------------------------------------------
# Dependency-resolution failures: we never got as far as compiling.
# --------------------------------------------------------------------------
DEP_RESOLUTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("dep_revision_not_found", re.compile(r"revision not found")),
    # NOTE: `infra_release_fetch` is ALSO treated as dependency-origin, via
    # DEP_RESOLUTION_CLASSES below. It reproduced red locally in every carrier
    # we built, so a pinned release artifact that no longer downloads is a real
    # persistent dependency-acquisition failure, not a runner flake.
    ("dep_clone_failed", re.compile(r"(failed to clone|repository not found|fatal: could not read)", re.I)),
    ("dep_manifest_mismatch", re.compile(r"(manifest.*out of date|no such package|missing manifest)", re.I)),
]

# --------------------------------------------------------------------------
# Lake / build-config breakage (the lakefile + toolchain churn class).
# --------------------------------------------------------------------------
LAKE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("lake_config_api", re.compile(r"is not a field of structure `(Lake\.|Option\.Decl)", re.I)),
    ("lake_duplicate_root", re.compile(r"has the same root module", re.I)),
    ("lake_unknown_option", re.compile(r"unknown (option|package|target|configuration)", re.I)),
    ("lake_toolchain_conflict", re.compile(r"toolchain not updated; multiple toolchain candidates", re.I)),
    # The repo's pinned Lake is too old to understand a command Reservoir uses.
    ("lake_unknown_command", re.compile(r"unknown command '", re.I)),
    ("lake_no_config", re.compile(r"no configuration file with a supported extension", re.I)),
    # Stale/absent build artifacts from a mismatched toolchain.
    ("lake_stale_artifact", re.compile(r"object file '.*' of module .* does not exist", re.I)),
    ("lake_external_command", re.compile(r"external command '.*' exited with code", re.I)),
    ("lake_missing_file", re.compile(r"no such file or directory \(error code", re.I)),
]

# --------------------------------------------------------------------------
# Lean elaboration errors -- the real mathematical/API breakage.
# Ordered most-specific first; all matching classes are recorded.
# --------------------------------------------------------------------------
LEAN_PATTERNS: list[tuple[str, re.Pattern]] = [
    # `not found in the provided declarations` is how a removed/renamed constant
    # surfaces through Mathlib's Cache tooling -- same root cause as a rename.
    (
        "unknown_identifier",
        re.compile(r"(unknown (identifier|constant|declaration)|not found in the provided declarations)", re.I),
    ),
    ("unknown_namespace", re.compile(r"unknown namespace", re.I)),
    ("deprecated", re.compile(r"has been deprecated", re.I)),
    ("not_a_field", re.compile(r"is not a field of structure", re.I)),
    ("invalid_field", re.compile(r"invalid field `", re.I)),
    ("type_mismatch", re.compile(r"type mismatch", re.I)),
    (
        "failed_to_synth",
        re.compile(r"(failed to synthesize|typeclass instance problem is stuck)", re.I),
    ),
    ("instance_binder", re.compile(r"invalid binder annotation", re.I)),
    ("invalid_instance", re.compile(r"should not be an instance", re.I)),
    ("duplicate_declaration", re.compile(r"has already been declared", re.I)),
    ("missing_cases", re.compile(r"Missing cases:", re.I)),
    # Backticks around tactic names appear in newer Lean: `` `simp` made no progress ``
    (
        "tactic_failed",
        re.compile(r"(tactic '\w+' failed|`?\w+`? made no progress|linarith failed)", re.I),
    ),
    ("unsolved_goals", re.compile(r"unsolved goals", re.I)),
    ("recursion_depth", re.compile(r"maximum recursion depth", re.I)),
    (
        "deterministic_timeout",
        re.compile(r"((deterministic|whnf) timeout|maximum number of heartbeats)", re.I),
    ),
    ("ambiguous", re.compile(r"(ambiguous|overloaded, errors)", re.I)),
    (
        "syntax_error",
        re.compile(r"(unexpected token|expected .* but got|invalid pattern)", re.I),
    ),
    ("compiler_ir", re.compile(r"was not compiled; .* must run on inductive types first", re.I)),
    ("sorry_present", re.compile(r"declaration uses 'sorry'", re.I)),
]

ALL_PATTERNS = INFRA_PATTERNS + DEP_RESOLUTION_PATTERNS + LAKE_PATTERNS + LEAN_PATTERNS

INFRA_CLASSES = {name for name, _ in INFRA_PATTERNS}
# A pinned release artifact that no longer downloads is dependency acquisition
# failing, which is dependency rot of the most literal kind.
DEP_RESOLUTION_CLASSES = {name for name, _ in DEP_RESOLUTION_PATTERNS} | {
    "infra_release_fetch"
}

# `error: Path/To/File.lean:12:3: message` -- the only lines that tell us WHOSE
# code failed. Lake also emits bare `error: build failed`, which tells us nothing.
ERROR_FILE_RE = re.compile(r"^error: (?P<path>[^\s:]+\.lean):\d+:\d+:", re.M)
# Errors inside a materialised dependency checkout.
LAKE_PACKAGE_RE = re.compile(r"\.lake/packages/(?P<dep>[^/]+)/")


@dataclass
class LogVerdict:
    error_classes: list[str] = field(default_factory=list)
    failure_origin: str = "unknown"
    is_infra: bool = False
    first_error: str | None = None
    error_files: list[str] = field(default_factory=list)
    dep_names_hit: list[str] = field(default_factory=list)


def classify_log(lines: list[str], dep_names: set[str]) -> LogVerdict:
    """Classify one de-timestamped build log.

    `dep_names` is the package's declared dependency names, lowercased. A Lean
    error whose path begins with a dependency's name is that dependency's
    breakage, not the package's -- Reservoir reports dependency paths relative
    to the dependency root rather than under .lake/packages.
    """
    text = "\n".join(lines)
    v = LogVerdict()

    for name, pat in ALL_PATTERNS:
        if pat.search(text):
            v.error_classes.append(name)

    v.is_infra = bool(set(v.error_classes) & INFRA_CLASSES)

    for line in lines:
        if line.startswith("error:") and "build failed" not in line:
            v.first_error = line[:500]
            break

    self_hit = dep_hit = False
    seen_files: list[str] = []
    hit_deps: set[str] = set()

    for m in ERROR_FILE_RE.finditer(text):
        path = m.group("path")
        if path not in seen_files:
            seen_files.append(path)

        pkg_match = LAKE_PACKAGE_RE.search(path)
        if pkg_match:
            dep_hit = True
            hit_deps.add(pkg_match.group("dep").lower())
            continue

        top = path.split("/")[0].lower()
        if top in dep_names:
            dep_hit = True
            hit_deps.add(top)
        else:
            self_hit = True

    # Dependency resolution failed before any compilation: the breakage is in
    # the dependency edge, not in this package's source.
    if set(v.error_classes) & DEP_RESOLUTION_CLASSES:
        dep_hit = True

    if self_hit and dep_hit:
        v.failure_origin = "both"
    elif dep_hit:
        v.failure_origin = "dependency"
    elif self_hit:
        v.failure_origin = "self"
    else:
        v.failure_origin = "unknown"

    # Cap generously rather than tightly: error_file_count is used to rank how
    # bad a breakage is, and a cap of 20 saturated on every serious package,
    # making the ranking meaningless.
    v.error_files = seen_files[:200]
    v.dep_names_hit = sorted(hit_deps)
    return v
