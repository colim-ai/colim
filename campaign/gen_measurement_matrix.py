"""Emit the Actions matrix for measuring the `infra_uninformative` cohort.

These packages are red in Reservoir's data only because its runner failed to
fetch a build cache. We measure rather than assume: rebuild each one in OUR OWN
repository, upstream cloned read-only at its pinned revision, evaluation
toolchain forced, and link the public run log per package.

Nothing here forks, pushes, or contacts upstream.

  python3 campaign/gen_measurement_matrix.py            # print chunk 1
  python3 campaign/gen_measurement_matrix.py --chunk 2  # next chunk
  python3 campaign/gen_measurement_matrix.py --dispatch # run it via gh
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import EVAL_TOOLCHAIN, assert_silent_week, gh_org  # noqa: E402
from ledger.db import connect  # noqa: E402

# GitHub caps a matrix at 256 jobs. Stay well under, and spread across days so
# the shared Actions quota is not monopolised by measurement.
CHUNK_SIZE = 40


def cohort(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT pkg_key, repo_url, eval_revision, mathlib_downstream FROM packages "
        "WHERE in_k=1 AND final_status='RED_REGRESSION' "
        "AND red_basis='infra_uninformative' AND repo_url IS NOT NULL "
        "AND source_available=1 ORDER BY stars DESC, pkg_key"
    ).fetchall()
    return [
        {
            "pkg_key": r["pkg_key"],
            "repo_url": r["repo_url"],
            "revision": r["eval_revision"],
            "toolchain": EVAL_TOOLCHAIN,
            "mathlib": "true" if r["mathlib_downstream"] else "false",
        }
        for r in rows
        if r["eval_revision"]
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", type=int, default=1)
    ap.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    ap.add_argument("--dispatch", action="store_true", help="trigger the workflow via gh")
    args = ap.parse_args()

    assert_silent_week()

    conn = connect()
    items = cohort(conn)
    chunks = [
        items[i : i + args.chunk_size] for i in range(0, len(items), args.chunk_size)
    ]
    if not chunks:
        raise SystemExit("cohort is empty -- run harness/red_basis.py first")

    if not 1 <= args.chunk <= len(chunks):
        raise SystemExit(f"--chunk must be 1..{len(chunks)} ({len(items)} packages)")

    payload = json.dumps(chunks[args.chunk - 1])
    print(
        f"cohort {len(items)} packages, {len(chunks)} chunk(s) of {args.chunk_size}; "
        f"emitting chunk {args.chunk} ({len(chunks[args.chunk - 1])} jobs)",
        file=sys.stderr,
    )

    if not args.dispatch:
        print(payload)
        return

    # Runs in our own repo. Never a fork, never upstream.
    repo = f"{gh_org()}/colim"
    cmd = [
        "gh", "workflow", "run", "measure-reds.yml",
        "--repo", repo,
        "-f", f"matrix={payload}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit(f"dispatch failed: {proc.stderr.strip()}")
    print(f"dispatched chunk {args.chunk} to {repo}", file=sys.stderr)


if __name__ == "__main__":
    main()
