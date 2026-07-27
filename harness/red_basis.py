"""Assign `red_basis` to every red package: WHY do we believe it is red?

Ruling 2026-07-27. The infra cohort splits in two, and the two halves may not
be quoted the same way:

  infra_release        a pinned release artifact no longer downloads. This
                       reproduced red locally in every carrier we built, so it
                       is a genuine, persistent dependency-acquisition failure.
                       Stays in M; origin is `dependency`.
  infra_uninformative  the only failure was a cache fetch. Reservoir told us
                       nothing about whether the code survives. Stays in M for
                       now -- its recorded outcome IS red -- but flagged, and
                       resolved by measurement rather than by assumption.

A local or Actions reproduction outranks both: once we have built it ourselves,
the basis is what we measured, not what we inferred from someone's log.

Idempotent; rerun after any reclassification or new reproduction.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from errors import INFRA_CLASSES  # noqa: E402
from ledger.db import connect, now  # noqa: E402


def basis_for(classes: set[str], measured: str | None) -> str:
    if measured:
        return measured
    non_infra = classes - INFRA_CLASSES
    if non_infra:
        return "code_error"
    if "infra_release_fetch" in classes:
        return "infra_release"
    if classes:
        return "infra_uninformative"
    # No classes at all should not happen after classifier v0, but if it does,
    # "we do not know" is the honest answer rather than a default to code_error.
    return "infra_uninformative"


def main() -> None:
    conn = connect()

    # Conclusive reproductions win. An inconclusive one (compiled nothing)
    # deliberately does NOT count as a measurement.
    measured = {
        r["pkg_key"]: "measured_actions" if r["toolchain_source"] == "actions" else "measured_local"
        for r in conn.execute(
            "SELECT pkg_key, COALESCE(conclusive,1) AS conc, "
            "'local' AS toolchain_source FROM reproductions "
            "WHERE COALESCE(conclusive,1)=1"
        ).fetchall()
    }

    rows = conn.execute(
        "SELECT pkg_key, error_classes FROM packages "
        "WHERE in_k=1 AND final_status='RED_REGRESSION'"
    ).fetchall()

    counts: dict[str, int] = {}
    for r in rows:
        classes = set(json.loads(r["error_classes"] or "[]"))
        basis = basis_for(classes, measured.get(r["pkg_key"]))
        counts[basis] = counts.get(basis, 0) + 1
        conn.execute(
            "UPDATE packages SET red_basis=?, updated_at=? WHERE pkg_key=?",
            (basis, now(), r["pkg_key"]),
        )
    conn.commit()

    print(f"red_basis assigned over {len(rows)} reds:")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<22}{v:>5}")


if __name__ == "__main__":
    main()
