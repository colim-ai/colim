"""Identifier-boundary-aware rewriting of renamed Lean declarations.

Tier 1's job is the mechanical half of repair: apply upstream's own rename map
to downstream source. The two ways to get this wrong are rewriting inside
strings and comments, and rewriting a substring of a longer identifier. Both
are handled by lexing first and only touching code regions.

A bad rewrite yields a red build, never a false green -- the kernel is the net
-- so this does not need to be conservative to the point of uselessness. It
does need to be honest about what it changed, which is why every substitution
is returned for the ledger.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

RENAMES_PATH = Path(__file__).resolve().parent / "renames.json"
SUPPLEMENT_PATH = Path(__file__).resolve().parent / "renames_supplement.toml"

# Lean identifiers admit primes, bangs, question marks, subscripts and Greek.
IDENT_CHARS = r"A-Za-z0-9_'!?₀-₉ₐ-ₜα-ωΑ-Ωℕℤℚℝℂ"
TOKEN_RE = re.compile(rf"«[^»]*»|[{IDENT_CHARS}]+(?:\.[{IDENT_CHARS}]+)*")


@dataclass
class Substitution:
    old: str
    new: str
    line: int
    col: int


def code_spans(text: str) -> list[tuple[int, int]]:
    """Spans of `text` that are real code -- strings and comments excluded.

    Handles Lean's line comments, NESTED block comments (`/- /- -/ -/`), doc
    comments, string literals with escapes, and char literals. Nesting matters:
    treating `/-` .. `-/` as non-nesting would end a comment early and expose
    commented-out code to rewriting.
    """
    spans: list[tuple[int, int]] = []
    i, n = 0, len(text)
    start = 0
    while i < n:
        c = text[i]
        if c == "-" and text.startswith("--", i):
            spans.append((start, i))
            j = text.find("\n", i)
            i = n if j < 0 else j
            start = i
        elif c == "/" and text.startswith("/-", i):
            spans.append((start, i))
            depth, i = 1, i + 2
            while i < n and depth:
                if text.startswith("/-", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("-/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            start = i
        elif c == '"':
            spans.append((start, i))
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            start = i
        elif c == "'" and i + 2 < n and (text[i + 1] != "'" ):
            # Char literal only when it closes shortly; otherwise it is a prime
            # inside an identifier (`foo'`), which must stay in code.
            m = re.match(r"'(\\.|[^'\\])'", text[i:])
            if m:
                spans.append((start, i))
                i += m.end()
                start = i
            else:
                i += 1
        else:
            i += 1
    spans.append((start, n))
    return [(a, b) for a, b in spans if a < b]


def load_supplement(path: Path = SUPPLEMENT_PATH) -> list[dict]:
    """Curated entries for renames extraction cannot see.

    Every entry must carry an evidence link; an unevidenced entry is a
    recollection, and this project does not act on those. `config` entries are
    build-configuration changes and are deliberately NOT identifier rewrites --
    they belong to tier-1b, so they are excluded from the rewrite map here.
    """
    if not path.exists():
        return []
    import tomllib

    with path.open("rb") as f:
        doc = tomllib.load(f)

    entries = doc.get("entry") or []
    for e in entries:
        missing = [k for k in ("old", "new", "kind", "evidence", "source") if not e.get(k)]
        if missing:
            raise SystemExit(
                f"supplement entry {e.get('old', '?')} is missing {missing}; "
                "evidence and provenance are required for every entry"
            )
        if e["kind"] not in {"rename", "hard_removal", "config"}:
            raise SystemExit(f"supplement entry {e['old']}: unknown kind {e['kind']!r}")
    return entries


def load_map(
    path: Path = RENAMES_PATH, supplement: Path = SUPPLEMENT_PATH
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (qualified_map, unambiguous_short_map)."""
    doc = json.loads(path.read_text())
    ambiguous = set(doc.get("ambiguous_short_names") or [])

    qualified: dict[str, str] = {}
    short: dict[str, str] = {}
    for r in doc["renames"]:
        if r["old"] and r["new"] and r["old"] != r["new"]:
            qualified[r["old"]] = r["new"]
        # Short names are only safe when they resolve to exactly one target.
        if r["old_short"] not in ambiguous and r["old_short"] != r["new_short"]:
            short[r["old_short"]] = r["new_short"]

    # Curated entries are applied last and win on conflict: they exist
    # precisely because extraction got the wrong answer or no answer.
    for e in load_supplement(supplement):
        if e["kind"] == "config":
            continue  # tier-1b handles these; never an identifier rewrite
        qualified[e["old"]] = e["new"]
        old_short, new_short = e["old"].split(".")[-1], e["new"].split(".")[-1]
        if old_short not in ambiguous and old_short != new_short:
            short[old_short] = new_short

    return qualified, short


def rewrite_text(
    text: str, qualified: dict[str, str], short: dict[str, str]
) -> tuple[str, list[Substitution]]:
    """Apply the rename map to code regions only.

    Matching is whole-token: `Foo.bar` never rewrites inside `Foo.barbaz`, and
    a bare `trim` is only touched when its short name is unambiguous across the
    entire map.
    """
    subs: list[Substitution] = []
    out: list[str] = []
    cursor = 0

    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)

    def position(idx: int) -> tuple[int, int]:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= idx:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1, idx - line_starts[lo] + 1

    for a, b in code_spans(text):
        out.append(text[cursor:a])
        segment = text[a:b]
        last = 0
        pieces: list[str] = []
        for m in TOKEN_RE.finditer(segment):
            token = m.group(0)
            replacement = qualified.get(token)
            if replacement is None and "." not in token:
                replacement = short.get(token)
            if replacement is None or replacement == token:
                continue
            pieces.append(segment[last : m.start()])
            pieces.append(replacement)
            last = m.end()
            line, col = position(a + m.start())
            subs.append(Substitution(token, replacement, line, col))
        pieces.append(segment[last:])
        out.append("".join(pieces))
        cursor = b

    out.append(text[cursor:])
    return "".join(out), subs


# `[ \t]*` rather than `\s*`: a trailing `\s*$` swallows the newline itself,
# and the replacement then silently welds the next line onto this one.
IMPORT_RE = re.compile(
    r"^(?P<indent>[ \t]*)import[ \t]+(?P<mod>[A-Za-z_][A-Za-z0-9_.]*)[ \t]*$", re.M
)


def rewrite_imports(text: str, module_map: dict[str, list[str]]) -> tuple[str, list[Substitution]]:
    """Rewrite `import Old.Module` using the tier-1c module map.

    A split module expands to imports of ALL its children: importing the
    superset is the highest-probability repair, and a wrong guess costs a red
    build rather than a false green, so verification arbitrates.

    Duplicate imports introduced by two old modules splitting into overlapping
    children are collapsed, since Lean would warn on them.
    """
    subs: list[Substitution] = []
    seen_imports: set[str] = set()

    for m in IMPORT_RE.finditer(text):
        seen_imports.add(m.group("mod"))

    def replace(m: re.Match) -> str:
        old = m.group("mod")
        new = module_map.get(old)
        if not new:
            return m.group(0)
        indent = m.group("indent")
        line = text[: m.start()].count("\n") + 1
        subs.append(Substitution(old, " + ".join(new), line, len(indent) + 1))
        # Drop children already imported elsewhere in the file.
        emit = [n for n in new if n not in seen_imports or n == old]
        seen_imports.update(emit)
        return "\n".join(f"{indent}import {n}" for n in emit) or m.group(0)

    return IMPORT_RE.sub(replace, text), subs


def rewrite_file(
    path: Path,
    qualified: dict[str, str],
    short: dict[str, str],
    *,
    module_map: dict[str, list[str]] | None = None,
    dry_run: bool = False,
) -> list[Substitution]:
    original = path.read_text(errors="replace")
    updated, subs = rewrite_text(original, qualified, short)
    if module_map:
        updated, import_subs = rewrite_imports(updated, module_map)
        subs = subs + import_subs
    if subs and not dry_run and updated != original:
        path.write_text(updated)
    return subs


def rewrite_tree(
    root: Path,
    qualified: dict[str, str],
    short: dict[str, str],
    *,
    module_map: dict[str, list[str]] | None = None,
    dry_run: bool = False,
) -> dict[str, list[Substitution]]:
    """Rewrite a package's own sources, never its vendored dependencies."""
    changes: dict[str, list[Substitution]] = {}
    for path in sorted(root.rglob("*.lean")):
        if ".lake" in path.parts:
            continue  # dependency checkouts are upstream's problem, not ours
        subs = rewrite_file(path, qualified, short, module_map=module_map, dry_run=dry_run)
        if subs:
            changes[str(path.relative_to(root))] = subs
    return changes
