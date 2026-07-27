"""Tests for the tier-1 rewriter.

The dangerous failure here is silent: rewriting inside a string or a comment,
or clipping a substring out of a longer identifier, produces source that still
looks plausible. These pin the lexer against exactly those cases.

Run: python3 fixers/tier1/test_rewrite.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rewrite import code_spans, rewrite_text  # noqa: E402

Q = {"String.trim": "String.trimAscii", "Foo.old": "Foo.new"}
S = {"oldName": "newName"}


def rw(text: str) -> str:
    return rewrite_text(text, Q, S)[0]


def test_basic_qualified_rename():
    assert rw("example := String.trim s") == "example := String.trimAscii s"


def test_unambiguous_short_rename():
    assert rw("theorem t := oldName") == "theorem t := newName"


def test_never_rewrites_substring_of_longer_identifier():
    """`String.trimLeft` must not become `String.trimAsciiLeft`."""
    assert rw("String.trimLeft s") == "String.trimLeft s"
    assert rw("oldNameSuffix") == "oldNameSuffix"
    assert rw("prefix_oldName") == "prefix_oldName"


def test_skips_line_comments():
    src = "-- String.trim is deprecated\nString.trim x"
    assert rw(src) == "-- String.trim is deprecated\nString.trimAscii x"


def test_skips_block_comments():
    src = "/- String.trim -/ String.trim x"
    assert rw(src) == "/- String.trim -/ String.trimAscii x"


def test_skips_nested_block_comments():
    """Non-nesting handling would end the comment early and rewrite inside it."""
    src = "/- outer /- String.trim -/ still comment -/ String.trim x"
    out = rw(src)
    assert out.startswith("/- outer /- String.trim -/ still comment -/")
    assert out.endswith("String.trimAscii x")


def test_skips_doc_comments():
    src = "/-- doc mentions String.trim -/\nString.trim x"
    assert "doc mentions String.trim" in rw(src)
    assert rw(src).endswith("String.trimAscii x")


def test_skips_string_literals():
    src = 'IO.println "call String.trim here"\nString.trim x'
    out = rw(src)
    assert '"call String.trim here"' in out
    assert out.endswith("String.trimAscii x")


def test_string_with_escaped_quote():
    src = 'let s := "a \\" String.trim b"\nString.trim x'
    out = rw(src)
    assert 'String.trim b"' in out
    assert out.endswith("String.trimAscii x")


def test_prime_in_identifier_is_not_a_char_literal():
    """`foo'` must not open a char literal and swallow the rest of the line."""
    src = "theorem foo' := String.trim x"
    assert rw(src) == "theorem foo' := String.trimAscii x"


def test_char_literal_is_skipped():
    src = "let c := 'a'\nString.trim x"
    assert rw(src).endswith("String.trimAscii x")


def test_guillemet_identifier_left_alone():
    src = "lean_lib «String.trim» \nString.trim x"
    out = rw(src)
    assert "«String.trim»" in out


def test_no_change_returns_identical_text():
    src = "theorem untouched := Nat.succ 0\n"
    out, subs = rewrite_text(src, Q, S)
    assert out == src and subs == []


def test_substitutions_are_reported_with_positions():
    _, subs = rewrite_text("x\nString.trim y", Q, S)
    assert len(subs) == 1
    assert subs[0].old == "String.trim" and subs[0].new == "String.trimAscii"
    assert subs[0].line == 2


def test_code_spans_cover_plain_code():
    assert code_spans("abc") == [(0, 3)]


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
    print(f"\n{'FAILED' if failed else 'all rewriter tests passed'}")
    sys.exit(1 if failed else 0)
