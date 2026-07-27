"""Day-1 decision: which toolchain do we evaluate against?

Rule (CLAUDE.md): the most recent stable release with a recorded outcome for
>=80% of K. Coverage counts recorded outcomes only -- attempted-but-unrecorded
is indistinguishable from unattempted. If nothing clears 80%, take the newest
stable maximising coverage and print the coverage % everywhere.

Prints a table; changes nothing. Writing the choice to ledger.meta is a
separate, deliberate step.
"""

from __future__ import annotations

import argparse
from collections import Counter

from reservoir import is_stable, load_all, snapshot_commit, version_key

THRESHOLD = 0.80


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="include rc/nightly rows")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    pkgs = load_all()
    k_indexed = len(pkgs)

    recorded: Counter[str] = Counter()
    green: Counter[str] = Counter()
    for p in pkgs:
        for tc in {b.toolchain for b in p.builds}:
            recorded[tc] += 1
            if p.outcome_on(tc):
                green[tc] += 1

    rows = [tc for tc in recorded if args.all or is_stable(tc)]
    rows.sort(key=version_key, reverse=True)

    print(f"snapshot   {snapshot_commit()}")
    print(f"K_indexed  {k_indexed}  (all indexed packages; archived not yet excluded)")
    print()
    print(f"{'toolchain':<34}{'recorded':>9}{'cover':>8}{'green':>7}{'red':>6}{'red%':>7}")
    print("-" * 71)
    for tc in rows[: args.top]:
        n = recorded[tc]
        g = green[tc]
        red = n - g
        cover = n / k_indexed
        flag = "  <- clears 80%" if cover >= THRESHOLD and is_stable(tc) else ""
        print(
            f"{tc:<34}{n:>9}{cover:>7.1%}{g:>7}{red:>6}{red / n if n else 0:>7.1%}{flag}"
        )

    eligible = [tc for tc in rows if is_stable(tc) and recorded[tc] / k_indexed >= THRESHOLD]
    print()
    if eligible:
        pick = max(eligible, key=version_key)
        print(f"EVALUATION TOOLCHAIN: {pick}  (coverage {recorded[pick] / k_indexed:.1%})")
    else:
        best = max((tc for tc in rows if is_stable(tc)), key=lambda t: recorded[t])
        print(
            f"NO stable toolchain clears {THRESHOLD:.0%}. "
            f"Max-coverage stable: {best} at {recorded[best] / k_indexed:.1%} "
            "-- coverage % must be printed beside every headline number."
        )


if __name__ == "__main__":
    main()
