"""Fixture tests for the curated rename supplement.

Ruling: every supplement entry must be fixture-tested and must carry an
evidence link. These tests enforce both, so an entry cannot be added on
recollection alone or without a case demonstrating what it fixes.

Run: python3 fixers/tier1/test_supplement.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rewrite import load_map, load_supplement, rewrite_text  # noqa: E402

EVIDENCE_PREFIXES = ("https://github.com/", "https://leanprover", "https://lean-lang.org")


def test_every_entry_has_evidence_and_provenance():
    for e in load_supplement():
        assert e["evidence"].startswith(EVIDENCE_PREFIXES), (
            f"{e['old']}: evidence must be a real upstream link, got {e['evidence']!r}"
        )
        assert e["source"] in {"human", "agent"}, f"{e['old']}: bad source"


def test_every_entry_has_a_fixture():
    """Each entry must appear in FIXTURES below, so nothing lands untested."""
    covered = {old for old, _, _ in FIXTURES}
    for e in load_supplement():
        assert e["old"] in covered, (
            f"{e['old']} has no fixture case in test_supplement.py -- "
            "every supplement entry must be demonstrated"
        )


# (old, source snippet, expected snippet after rewriting)
FIXTURES = [
    (
        "Array.mkArray",
        "def zeros : Array Nat := Array.mkArray 5 0",
        "def zeros : Array Nat := Array.replicate 5 0",
    ),
]


def test_fixtures_rewrite_as_expected():
    qualified, short = load_map()
    for old, src, expected in FIXTURES:
        out, _ = rewrite_text(src, qualified, short)
        assert out == expected, f"{old}: got {out!r}, expected {expected!r}"


def test_mkArray_numbered_variants_are_untouched():
    """Array.mkArray0..4 are unrelated declarations that still exist."""
    qualified, short = load_map()
    src = "Array.mkArray0 , Array.mkArray2 x y"
    out, _ = rewrite_text(src, qualified, short)
    assert out == src, out


def test_supplement_overrides_extracted_map():
    qualified, _ = load_map()
    for e in load_supplement():
        if e["kind"] != "config":
            assert qualified.get(e["old"]) == e["new"], (
                f"{e['old']} should resolve to the curated target {e['new']}"
            )


def test_config_entries_never_enter_the_rewrite_map():
    qualified, short = load_map()
    for e in load_supplement():
        if e["kind"] == "config":
            assert qualified.get(e["old"]) != e["new"], (
                f"{e['old']} is a config change and must be routed to tier-1b, "
                "not rewritten as an identifier"
            )


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
    print(f"\n{'FAILED' if failed else 'all supplement tests passed'}")
    sys.exit(1 if failed else 0)
