"""Run tier 1 over packages and measure the fix rate.

Tier 1 is: bump the toolchain and Mathlib pins to the campaign target, then
apply upstream's own rename map to the package's own sources. Nothing here is
clever, and it does not need to be -- a bad rewrite yields a red build, never a
false green, because the kernel is the check.

Verification goes through harness.build's shared path, so "green" means the
same thing here as everywhere else: lake succeeded AND something compiled.

  python3 fixers/tier1/run_tier1.py --sample      # the reproduced sample
  python3 fixers/tier1/run_tier1.py --packages a/b c/d
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
for p in (REPO_ROOT, REPO_ROOT / "harness", HERE):
    sys.path.insert(0, str(p))

from build import (  # noqa: E402
    DiskFloorExceeded,
    assert_toolchain_intact,
    build_package,
    clone_at,
    free_gb,
    tree_path,
)
from bump import bump  # noqa: E402
from config import BUILD, CONFIG, EVAL_TOOLCHAIN  # noqa: E402
from ledger.db import connect, now  # noqa: E402
from rewrite import load_map, rewrite_tree  # noqa: E402

TARGET = CONFIG["campaign_target"]

# Per-class outcome reporting is a headline product metric, not a detail:
# "tier 1 fixes X% of renames" is a very different claim from "tier 1 fixes X%
# of red packages", and only the first is what tier 1 is actually for.
OUTCOMES = [
    "renames_fixed",
    "hard_removal_blocked",
    "import_blocked",
    "config_blocked",
    "semantic_blocked",
    "other_blocked",
    "invalid",
]


def blocker_class(log_text: str, qualified: dict[str, str], short: dict[str, str]) -> str:
    """Why did tier 1 fail to fix this package?

    Determined from the POST-tier-1 log, so it describes what is still broken
    after the rename map has been applied, not what was broken before.
    """
    from errors import SEMANTIC_CLASSES, TIER1B_CLASSES, UNKNOWN_IDENT_RE, classify_log

    classes = set(classify_log(log_text.splitlines(), set()).error_classes)

    if classes & TIER1B_CLASSES:
        return "config_blocked"

    # A renamed MODULE, which the declaration rename map cannot express. This
    # is mechanically fixable and is the clearest tier-1 gap we have found.
    if "bad_import" in classes:
        return "import_blocked"

    # An identifier the compiler cannot find, which our maps also cannot map,
    # is a rename or removal upstream did not leave behind any trace of.
    unresolved = [
        name
        for name in UNKNOWN_IDENT_RE.findall(log_text)
        if name not in qualified and name.split(".")[-1] not in short
    ]
    if unresolved:
        return "hard_removal_blocked"

    if classes & SEMANTIC_CLASSES:
        return "semantic_blocked"
    return "other_blocked"


def reclassify(conn) -> None:
    """Derive per-class outcomes from stored tier-1 transcripts.

    The builds already ran; their logs are on disk and hashed into the ledger.
    Re-deriving the breakdown costs seconds, where rebuilding costs an hour.
    """
    qualified, short = load_map()
    rows = conn.execute(
        "SELECT pkg_key, tier1_result FROM packages WHERE tier1_result IS NOT NULL"
    ).fetchall()
    if not rows:
        raise SystemExit("no tier-1 results recorded yet")

    log_dir = REPO_ROOT / "data" / "build_logs"
    tally: dict[str, int] = {}
    detail: list[tuple[str, str]] = []

    for r in rows:
        info = json.loads(r["tier1_result"])
        if info.get("green"):
            outcome = "renames_fixed"
        else:
            digest = (info.get("log_sha256") or "")[:12]
            matches = list(log_dir.glob(f"*.{digest}.log")) if digest else []
            if not matches:
                outcome = "other_blocked"
            else:
                outcome = blocker_class(
                    matches[0].read_text(errors="replace"), qualified, short
                )
        tally[outcome] = tally.get(outcome, 0) + 1
        detail.append((r["pkg_key"], outcome))

    total = len(rows)
    print(f"tier-1 outcomes over {total} package(s):\n")
    for name in OUTCOMES:
        n = tally.get(name, 0)
        if n:
            print(f"  {name:<24}{n:>4}  {n / total:6.0%}")
    print()
    for key, outcome in sorted(detail, key=lambda x: x[1]):
        print(f"  {outcome:<24}{key}")


def targets(conn, args) -> list:
    if args.packages:
        qs = ",".join("?" * len(args.packages))
        return conn.execute(
            f"SELECT pkg_key, repo_url, eval_revision, mathlib_downstream "
            f"FROM packages WHERE pkg_key IN ({qs})",
            args.packages,
        ).fetchall()
    return conn.execute(
        "SELECT p.pkg_key, p.repo_url, p.eval_revision, p.mathlib_downstream "
        "FROM packages p JOIN reproductions r ON r.pkg_key = p.pkg_key "
        "WHERE COALESCE(r.conclusive,1)=1 ORDER BY p.pkg_key"
    ).fetchall()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--packages", nargs="*", default=[])
    ap.add_argument("--dry-run", action="store_true", help="rewrite only, no build")
    ap.add_argument("--reclassify", action="store_true",
                    help="re-derive per-class outcomes from stored logs")
    args = ap.parse_args()

    conn = connect()
    if args.reclassify:
        reclassify(conn)
        return

    rows = targets(conn, args)
    if not rows:
        raise SystemExit("no target packages; run harness/reproduce.py first")

    qualified, short = load_map()
    print(f"rename map: {len(qualified)} qualified, {len(short)} unambiguous short")
    print(f"target: {TARGET['toolchain']} + mathlib {TARGET['mathlib_tag']}")
    print(f"disk free: {free_gb():.1f} GB\n")

    fixed = attempted = 0
    tally: dict[str, int] = {}
    for i, r in enumerate(rows, 1):
        key = r["pkg_key"]
        dest = tree_path(key)
        print(f"[{i}/{len(rows)}] {key}")

        log: list[str] = []
        code = clone_at(r["repo_url"], r["eval_revision"], dest, log, 900)
        if code:
            print("  clone failed; skipping")
            continue

        b = bump(dest, mathlib_downstream=bool(r["mathlib_downstream"]))
        changes = rewrite_tree(dest, qualified, short, dry_run=args.dry_run)
        n_subs = sum(len(v) for v in changes.values())
        print(
            f"  bump: toolchain {b.toolchain_before} -> {b.toolchain_after}"
            + (f", mathlib {b.mathlib_before} -> {b.mathlib_after}" if b.mathlib_after else "")
        )
        print(f"  rewrite: {n_subs} substitution(s) across {len(changes)} file(s)")

        if args.dry_run:
            continue

        attempted += 1
        try:
            res = build_package(
                key,
                r["repo_url"],
                r["eval_revision"],
                EVAL_TOOLCHAIN,
                mathlib_downstream=bool(r["mathlib_downstream"]),
                lake_jobs=BUILD["lake_jobs"],
                disk_floor_gb=BUILD["disk_floor_gb"],
                # Critical: the tree already carries tier-1's edits.
                skip_clone=True,
            )
        except DiskFloorExceeded as e:
            print(f"  STOPPING: {e}")
            break

        # A green on a toolchain we did not pin is not a green for the campaign.
        try:
            assert_toolchain_intact(dest, TARGET["toolchain"], [])
        except RuntimeError as e:
            print(f"  INVALID: {e}")
            tally["invalid"] = tally.get("invalid", 0) + 1
            continue

        ok = res.verified_green
        fixed += int(ok)
        if ok:
            outcome = "renames_fixed"
        else:
            outcome = blocker_class(res.log_path.read_text(errors="replace"), qualified, short)
        tally[outcome] = tally.get(outcome, 0) + 1
        print(
            f"  {'GREEN (tier-1 fixed)' if ok else 'still red -> ' + outcome} "
            f"exit={res.exit_code} {res.duration_s:.0f}s targets={res.targets or 'default'}"
        )

        conn.execute(
            "UPDATE packages SET tier1_result=?, updated_at=? WHERE pkg_key=?",
            (
                json.dumps(
                    {
                        "green": ok,
                        "bump": asdict(b),
                        "substitutions": n_subs,
                        "files": sorted(changes),
                        "log_sha256": res.log_sha256,
                    }
                ),
                now(),
                key,
            ),
        )
        conn.commit()

    if attempted:
        print(f"\ntier-1 fix rate: {fixed}/{attempted} = {fixed / attempted:.0%}")
        print("\nper-class outcome (the metric that actually means something):")
        for name in OUTCOMES:
            n = tally.get(name, 0)
            if n:
                print(f"  {name:<24}{n:>4}  {n / attempted:6.0%}")


if __name__ == "__main__":
    main()
