"""Day-1 census report. Every number here is a query over the ledger.

Written to be read by a hostile skeptic: denominators are always named, the
regression/never-green/unknown split is never collapsed, and every judgement
call is printed rather than buried.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ledger.db import connect, get_meta  # noqa: E402


def rule(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


def main() -> None:
    conn = connect()
    q = lambda sql, *a: conn.execute(sql, a).fetchall()  # noqa: E731
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731

    eval_tc = get_meta(conn, "eval_toolchain")
    coverage = float(get_meta(conn, "eval_toolchain_coverage"))
    indexed = one("SELECT COUNT(*) FROM packages")

    print("=" * 72)
    print("COLIM DAY-1 CENSUS")
    print("=" * 72)
    print(f"index snapshot      {get_meta(conn, 'index_commit')}")
    print(f"                    committed {get_meta(conn, 'index_committed_at')}")
    print(f"github metadata     fetched {get_meta(conn, 'github_meta_fetched_at')}")
    print(f"evaluation toolchain {eval_tc}   (build coverage {coverage:.1%} of indexed)")

    rule("SCOPE")
    archived = one("SELECT COUNT(*) FROM packages WHERE archived=1")
    k = one("SELECT COUNT(*) FROM packages WHERE in_k=1")
    unavail = one("SELECT COUNT(*) FROM packages WHERE in_k=1 AND source_available=0")
    print(f"indexed packages              {indexed}")
    print(f"  archived (excluded from K)  {archived}")
    print(f"K (indexed, non-archived)     {k}")
    print(f"  of which source vanished    {unavail}  (kept in K; see caveats)")

    rule(f"CLASSIFICATION ON {eval_tc}  (denominator K = {k})")
    for row in q(
        "SELECT final_status s, COUNT(*) n FROM packages WHERE in_k=1 "
        "GROUP BY s ORDER BY n DESC"
    ):
        print(f"  {row['s']:<26}{row['n']:>5}   {row['n'] / k:>6.1%}")

    m = one("SELECT COUNT(*) FROM packages WHERE in_k=1 AND final_status='RED_REGRESSION'")
    never = one("SELECT COUNT(*) FROM packages WHERE in_k=1 AND final_status='NEVER_GREEN'")
    ahead = one(
        "SELECT COUNT(*) FROM packages WHERE in_k=1 AND final_status='UNKNOWN' "
        "AND status_reason LIKE 'pins a toolchain newer%'"
    )
    print()
    print(f"  M (RED_REGRESSION) = {m}   M/K = {m / k:.1%}")
    print(f"  NEVER_GREEN excluded from the headline, as required: {never}")
    print(f"  UNKNOWN incl. {ahead} pinned AHEAD of the eval toolchain (see below)")

    if ahead:
        rule("PINNED AHEAD OF THE EVAL TOOLCHAIN — deliberately NOT in M")
        print(
            "  Reservoir forces the toolchain, so these fail because their source is\n"
            "  too NEW for v4.32.1, not because upstream broke them. Counting a\n"
            "  forced-backwards failure as a regression would be indefensible.\n"
        )
        for r in q(
            "SELECT pkg_key, stars, old_toolchain FROM packages WHERE in_k=1 "
            "AND final_status='UNKNOWN' AND status_reason LIKE 'pins a toolchain newer%' "
            "ORDER BY stars DESC"
        ):
            print(f"  {r['stars']:>6}  {r['pkg_key']:<45}{r['old_toolchain']}")

    rule("MATHLIB-DOWNSTREAM SUBSET (campaign target, own denominator K_m)")
    km = one("SELECT COUNT(*) FROM packages WHERE in_k=1 AND mathlib_downstream=1")
    km_red = one(
        "SELECT COUNT(*) FROM packages WHERE in_k=1 AND mathlib_downstream=1 "
        "AND final_status='RED_REGRESSION'"
    )
    km_unknown = one(
        "SELECT COUNT(*) FROM packages WHERE in_k=1 AND mathlib_downstream IS NULL"
    )
    print(f"K_m (mathlib-dependent, in K)     {km}")
    print(f"  RED_REGRESSION within K_m       {km_red}   {km_red / km:.1%} of K_m")
    print(f"dependency data unavailable       {km_unknown}  (counted in K, not in K_m)")
    campaign = one(
        "SELECT COUNT(*) FROM packages WHERE in_k=1 AND mathlib_downstream=1 "
        "AND final_status='RED_REGRESSION' AND release_train=0"
    )
    print(f"Day-6 campaign set (K_m red, minus release train)   {campaign}")

    rule("RECENCY SLICE (staleness never excludes; shown because it will be asked)")
    for label, cutoff in (("12 months", "2025-07-27"), ("24 months", "2024-07-27")):
        tot = one("SELECT COUNT(*) FROM packages WHERE in_k=1 AND last_commit>=?", cutoff)
        red = one(
            "SELECT COUNT(*) FROM packages WHERE in_k=1 AND last_commit>=? "
            "AND final_status='RED_REGRESSION'",
            cutoff,
        )
        print(f"  active within {label:<10} K={tot:<5} M={red:<5} M/K={red / tot:.1%}")

    rule("TOP RED_REGRESSION BY STARS (Day-2 spot-check candidates)")
    print(f"  {'stars':>6}  {'package':<44}{'pinned toolchain':<26}last commit")
    for r in q(
        "SELECT pkg_key, stars, old_toolchain, last_commit, mathlib_downstream "
        "FROM packages WHERE in_k=1 AND final_status='RED_REGRESSION' "
        "AND release_train=0 AND source_available=1 "
        "ORDER BY stars DESC LIMIT 25"
    ):
        tc = (r["old_toolchain"] or "?").replace("leanprover/lean4:", "")
        mark = "M" if r["mathlib_downstream"] else " "
        print(
            f"  {r['stars']:>6}{mark} {r['pkg_key']:<44}{tc:<26}"
            f"{(r['last_commit'] or '?')[:10]}"
        )

    rule("CAVEATS THAT MUST TRAVEL WITH THESE NUMBERS")
    print(
        "1. Reservoir FORCES the toolchain: 'red on the eval toolchain' means the\n"
        "   package fails when built against it, not that the maintainer's own\n"
        "   pinned build is broken. That is the intended regression question, but\n"
        "   it must be stated in these words.\n"
        "2. failure_origin is 'unknown' for every red row until Day-2 log analysis.\n"
        "   The self/dependency split is NOT yet available and must not be claimed.\n"
        f"3. {unavail} packages in K no longer resolve on GitHub. They can never enter N.\n"
        "4. Classification uses Reservoir's recorded outcomes, not local rebuilds.\n"
        "   Day 2 reproduces a sample locally to check Reservoir against reality."
    )
    print()


if __name__ == "__main__":
    main()
