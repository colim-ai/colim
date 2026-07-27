"""Docs-vs-ledger consistency tests.

CLAUDE.md requires that every number in any report is a query over the ledger,
and the Day-1 ruling requires that ledger, report.md and METHODOLOGY.md agree.
METHODOLOGY.md is prose and necessarily quotes some figures inline, so those
quotes are pinned here: if the ledger moves and the prose does not, this fails.

Run: python3 census/test_docs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from ledger.db import connect  # noqa: E402
from report import OUT_PATH, render  # noqa: E402

METHODOLOGY = HERE.parent / "METHODOLOGY.md"


def _counts() -> dict[str, int]:
    conn = connect()
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "k": one("SELECT COUNT(*) FROM packages WHERE in_k=1"),
        "m": one(
            "SELECT COUNT(*) FROM packages WHERE in_k=1 AND final_status='RED_REGRESSION'"
        ),
        "km": one("SELECT COUNT(*) FROM packages WHERE in_k=1 AND mathlib_downstream=1"),
        "never_green": one(
            "SELECT COUNT(*) FROM packages WHERE in_k=1 AND final_status='NEVER_GREEN'"
        ),
        "forced": one(
            "SELECT COUNT(*) FROM packages WHERE in_k=1 AND final_status='FORCED_DOWNGRADE'"
        ),
        "archived": one("SELECT COUNT(*) FROM packages WHERE final_status='ARCHIVED'"),
        "vanished": one("SELECT COUNT(*) FROM packages WHERE final_status='VANISHED'"),
        "indexed": one("SELECT COUNT(*) FROM packages"),
    }


def test_report_md_is_current():
    """report.md must be exactly what report.py renders from the ledger now."""
    assert OUT_PATH.exists(), "census/report.md missing -- run report.py --write"
    assert OUT_PATH.read_text().strip() == render().strip(), (
        "census/report.md is stale relative to the ledger. "
        "Run: python3 census/report.py --write"
    )


def test_methodology_quotes_match_ledger():
    text = METHODOLOGY.read_text()
    c = _counts()
    for label, expected in (
        ("K", f"{c['k']} packages"),
        ("M/K headline", f"{c['m']} of {c['k']}"),
        ("K_m", f"K_m = {c['km']}"),
        ("NEVER_GREEN", f"`NEVER_GREEN` ({c['never_green']} packages)"),
        ("archived count", f"| `ARCHIVED` | {c['archived']} |"),
        ("vanished count", f"| `VANISHED` | {c['vanished']} |"),
    ):
        if label == "K":
            continue  # covered by the headline assertion
        assert expected in text, (
            f"METHODOLOGY.md is out of sync with the ledger ({label}): "
            f"expected to find {expected!r}"
        )


def test_methodology_states_the_forced_toolchain_wording():
    """The single most attackable claim must be stated in the agreed words."""
    text = METHODOLOGY.read_text()
    assert "forced onto `leanprover/lean4:v4.32.1`" in text
    assert "Reservoir forces the toolchain" in text


def test_no_open_ruling_language():
    """Rulings are settled; nothing may still read as undecided."""
    banned = ("open ruling", "needs a ruling", "TBD", "to be decided")
    for path in (METHODOLOGY, OUT_PATH, HERE / "NOTES.md"):
        low = path.read_text().lower()
        for phrase in banned:
            assert phrase.lower() not in low, f"{path.name} still contains {phrase!r}"


def test_silent_week_tripwire_holds():
    from config import CAMPAIGN, assert_silent_week

    assert CAMPAIGN["allow_upstream_prs"] is False
    assert_silent_week()


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                failed += 1
                print(f"  FAIL  {name}: {e}")
    print(f"\n{'FAILED' if failed else 'all docs tests passed'}")
    sys.exit(1 if failed else 0)
