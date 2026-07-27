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
    args = ap.parse_args()

    conn = connect()
    rows = targets(conn, args)
    if not rows:
        raise SystemExit("no target packages; run harness/reproduce.py first")

    qualified, short = load_map()
    print(f"rename map: {len(qualified)} qualified, {len(short)} unambiguous short")
    print(f"target: {TARGET['toolchain']} + mathlib {TARGET['mathlib_tag']}")
    print(f"disk free: {free_gb():.1f} GB\n")

    fixed = attempted = 0
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
            continue

        ok = res.verified_green
        fixed += int(ok)
        print(
            f"  {'GREEN (tier-1 fixed)' if ok else 'still red'} "
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


if __name__ == "__main__":
    main()
