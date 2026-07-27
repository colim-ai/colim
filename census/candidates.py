"""Day-2: propose hand-repair candidates for the founders.

The point of hand-repairing before building the fixer is to learn what the
pipeline must handle. So the two lists are chosen to teach different things:

  EASY  — mechanical, single-cause, fast to build. These should be repairable by
          a deterministic rewrite, and they are the specification for tier 1.
  MEATY — multi-file or semantic breakage where a rename map cannot help. These
          are the specification for tier 2, and the honest test of the guard.

Every pick prints its reasons, drawn from the ledger. Nothing is hand-picked.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ledger.db import connect  # noqa: E402

# Classes a deterministic rewrite can plausibly fix: renames, deprecations,
# structure-field churn, lakefile/config migration.
MECHANICAL = {
    "deprecated",
    "unknown_identifier",
    "unknown_namespace",
    "not_a_field",
    "lake_config_api",
    "lake_unknown_option",
    "lake_duplicate_root",
}

# Classes that need real proof work -- no rename map will save you.
SEMANTIC = {
    "unsolved_goals",
    "tactic_failed",
    "type_mismatch",
    "failed_to_synth",
    "missing_cases",
    "instance_binder",
    "deterministic_timeout",
    "recursion_depth",
}


def load_reds(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT pkg_key, repo_url, stars, mathlib_downstream, old_toolchain, "
        "last_commit, error_classes, failure_origin, first_error, "
        "error_file_count, infra_only, eval_build_url "
        "FROM packages WHERE in_k=1 AND final_status='RED_REGRESSION' "
        "AND error_classes IS NOT NULL"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["classes"] = set(json.loads(r["error_classes"]) or [])
        out.append(d)
    return out


def score_easy(d: dict) -> tuple | None:
    """Lower is better. None disqualifies."""
    if d["infra_only"] or d["failure_origin"] != "self":
        return None
    if not (d["classes"] & MECHANICAL) or (d["classes"] & SEMANTIC):
        return None
    # Deprioritise packages pinning a non-release toolchain (PR releases,
    # nightlies): the bump target is ill-defined for them, which is a separate
    # problem from the rename churn these picks are meant to exercise.
    tc = d["old_toolchain"] or ""
    odd_pin = 1 if ("pr-release" in tc or "nightly" in tc or not tc) else 0
    # Prefer: standard pin, few broken files, no mathlib (fast build), popular.
    return (
        odd_pin,
        d["error_file_count"] or 99,
        0 if not d["mathlib_downstream"] else 1,
        -(d["stars"] or 0),
    )


def score_meaty(d: dict) -> tuple | None:
    if d["infra_only"]:
        return None
    if not (d["classes"] & SEMANTIC):
        return None
    if not d["mathlib_downstream"]:
        return None
    # "Meaty" has to mean more than one broken file, or it is a tier-1 job.
    if (d["error_file_count"] or 0) < 3:
        return None
    # Rank by significance first, breakage second. Sorting by breakage alone
    # surfaces pathological teaching repos with hundreds of broken exercise
    # files, which teach us far less than a real library failing hard.
    return (
        -(d["stars"] or 0),
        -(d["error_file_count"] or 0),
    )


def signature(d: dict) -> str:
    """Normalised first-error, used to avoid proposing the same bug twice.

    Several packages break identically because a shared dependency broke (the
    `Batteries/Data/Array/Match.lean` binder failure hits many at once). Two of
    those teach one lesson, not two.
    """
    e = d["first_error"] or ""
    e = re.sub(r":\d+:\d+:", ":L:C:", e)
    e = re.sub(r"`[^`]*`", "`X`", e)
    return e[:120]


def dedupe(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for d in rows:
        sig = signature(d)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(d)
    return out


def describe(d: dict) -> list[str]:
    why = []
    origin = d["failure_origin"]
    why.append(f"failure origin: {origin}")
    why.append(f"{d['error_file_count']} file(s) with errors")
    cls = sorted(d["classes"])
    why.append("classes: " + (", ".join(cls) if cls else "none matched"))
    why.append(("mathlib-downstream" if d["mathlib_downstream"] else "no mathlib dependency")
               + f"; pins {(d['old_toolchain'] or '?').replace('leanprover/lean4:', '')}")
    if d["last_commit"]:
        why.append(f"last commit {d['last_commit'][:10]}")
    return why


def show(title: str, picks: list[dict], owner: str) -> None:
    print(f"\n{'=' * 74}\n{title}  —  for {owner}\n{'=' * 74}")
    for i, d in enumerate(picks, 1):
        print(f"\n{i}. {d['pkg_key']}   ({d['stars']}★)")
        print(f"   {d['repo_url']}")
        if d["first_error"]:
            print(f"   first error: {d['first_error'][:150]}")
        for line in describe(d):
            print(f"   - {line}")
        print(f"   reservoir log: {d['eval_build_url']}")


def main() -> None:
    conn = connect()
    reds = load_reds(conn)

    easy = dedupe(sorted((d for d in reds if score_easy(d) is not None), key=score_easy))
    meaty = dedupe(sorted((d for d in reds if score_meaty(d) is not None), key=score_meaty))

    print(f"{len(reds)} classified reds; {len(easy)} mechanical-looking, {len(meaty)} semantic")
    show("EASY — tier-1 specification", easy[:3], "Ari")
    show("MEATY — tier-2 + guard specification", meaty[:3], "Yao")
    print()


if __name__ == "__main__":
    main()
