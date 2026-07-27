"""Day-2: reproduce recorded red states locally, to check Reservoir against reality.

The census trusts Reservoir's recorded outcomes. This is the control: build a
sample of reds ourselves, under the same forced toolchain, and record whether we
agree. Disagreement is a finding, not an error -- it bounds how much the census
can be trusted.

  python3 harness/reproduce.py --sample 10
  python3 harness/reproduce.py --packages owner/name owner2/name2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "census"))

from build import BuildResult, DiskFloorExceeded, build_package, free_gb, prune_trees  # noqa: E402
from config import BUILD, EVAL_TOOLCHAIN  # noqa: E402
from errors import classify_log  # noqa: E402
from ledger.db import connect, now  # noqa: E402
from reservoir import load_all  # noqa: E402


def select_sample(conn, n: int, infra_quota: int = 3) -> list[dict]:
    """Stratified by error class, with a deliberate infra-only quota.

    Two jobs. Most of the sample spreads across error classes, so we learn how
    the harness copes with renames, Lake-config churn and dependency-resolution
    failures rather than one of them ten times.

    The rest is drawn from `infra_only` reds on purpose. Those failed on
    Reservoir's runner (cache fetch, toolchain download) with no code error at
    all, so a local build is the only way to find out whether the red is real.
    If they build green here, Reservoir's red was spurious and M is overstated
    by that fraction -- which is exactly what Day 2 exists to measure.
    """
    rows = conn.execute(
        "SELECT pkg_key, repo_url, eval_revision, mathlib_downstream, stars, "
        "error_classes, failure_origin, infra_only FROM packages "
        "WHERE final_status='RED_REGRESSION' AND repo_url IS NOT NULL "
        "AND error_classes IS NOT NULL ORDER BY stars DESC"
    ).fetchall()

    # Infra-only picks: prefer cheap ones (no mathlib) so the control is fast.
    infra = [r for r in rows if r["infra_only"]]
    infra.sort(key=lambda r: (bool(r["mathlib_downstream"]), -(r["stars"] or 0)))
    infra_picks = infra[:infra_quota]

    by_class: dict[str, list] = {}
    for r in rows:
        classes = json.loads(r["error_classes"]) or ["unclassified"]
        if r["infra_only"]:
            continue  # handled by the quota above
        for c in classes:
            by_class.setdefault(c, []).append(r)

    picked: list = list(infra_picks)
    seen: set[str] = {r["pkg_key"] for r in picked}
    # Round-robin over classes ordered by how common they are.
    order = sorted(by_class, key=lambda c: -len(by_class[c]))
    depth = 0
    while len(picked) < n and depth < 50:
        for c in order:
            if len(picked) >= n:
                break
            bucket = by_class[c]
            if depth < len(bucket):
                r = bucket[depth]
                if r["pkg_key"] not in seen:
                    seen.add(r["pkg_key"])
                    picked.append(r)
        depth += 1
    return picked


def record(conn, r, res: BuildResult, verdict) -> None:
    conn.execute(
        "INSERT INTO reproductions (pkg_key, revision, toolchain, reservoir_built, "
        "local_built, agrees, failed_step, exit_code, timed_out, duration_s, "
        "log_path, log_sha256, error_classes, failure_origin, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(pkg_key, toolchain) DO UPDATE SET "
        "local_built=excluded.local_built, agrees=excluded.agrees, "
        "failed_step=excluded.failed_step, exit_code=excluded.exit_code, "
        "timed_out=excluded.timed_out, duration_s=excluded.duration_s, "
        "log_path=excluded.log_path, log_sha256=excluded.log_sha256, "
        "error_classes=excluded.error_classes, failure_origin=excluded.failure_origin",
        (
            r["pkg_key"],
            res.revision,
            res.toolchain,
            0,  # every sampled package is a recorded red
            int(res.ok),
            int(res.ok is False),  # agrees iff we also failed
            None if res.ok else res.step,
            res.exit_code,
            int(res.timed_out),
            res.duration_s,
            str(res.log_path),
            res.log_sha256,
            json.dumps(verdict.error_classes if verdict else []),
            verdict.failure_origin if verdict else None,
            now(),
        ),
    )
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--packages", nargs="*", default=[])
    ap.add_argument("--force", action="store_true", help="rebuild already-reproduced packages")
    args = ap.parse_args()

    conn = connect()
    if args.packages:
        qmarks = ",".join("?" * len(args.packages))
        rows = conn.execute(
            f"SELECT pkg_key, repo_url, eval_revision, mathlib_downstream, stars "
            f"FROM packages WHERE pkg_key IN ({qmarks})",
            args.packages,
        ).fetchall()
    else:
        rows = select_sample(conn, args.sample or 10)

    done = {
        r[0]
        for r in conn.execute("SELECT pkg_key FROM reproductions").fetchall()
    }

    print(f"reproducing {len(rows)} packages on {EVAL_TOOLCHAIN}")
    print(f"disk free: {free_gb():.1f} GB (floor {BUILD['disk_floor_gb']} GB)\n")

    for i, r in enumerate(rows, 1):
        key = r["pkg_key"]
        if key in done and not args.force:
            print(f"[{i}/{len(rows)}] {key}: already reproduced, skipping")
            continue

        print(f"[{i}/{len(rows)}] {key} ...", flush=True)
        try:
            res = build_package(
                key,
                r["repo_url"],
                r["eval_revision"],
                EVAL_TOOLCHAIN,
                mathlib_downstream=bool(r["mathlib_downstream"]),
                lake_jobs=BUILD["lake_jobs"],
                disk_floor_gb=BUILD["disk_floor_gb"],
            )
        except DiskFloorExceeded as e:
            print(f"  STOPPING: {e}")
            break

        verdict = classify_log(res.log_path.read_text().splitlines(), set())
        record(conn, r, res, verdict)

        agree = "AGREES (red)" if not res.ok else "DISAGREES -- built GREEN locally"
        print(
            f"  {agree}  step={res.step} exit={res.exit_code} "
            f"{res.duration_s:.0f}s  classes={verdict.error_classes[:4]}"
        )

        removed = prune_trees(keep={key}, cap=BUILD["build_tree_lru_cap"])
        if removed:
            print(f"  pruned {len(removed)} old build tree(s)")

    n = conn.execute("SELECT COUNT(*) FROM reproductions").fetchone()[0]
    agr = conn.execute("SELECT COUNT(*) FROM reproductions WHERE agrees=1").fetchone()[0]
    print(f"\nreproductions: {agr}/{n} agree with Reservoir")


if __name__ == "__main__":
    main()
