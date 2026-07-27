"""Extract deprecation/rename maps from Lean sources at the evaluation revision.

Tier 1 is deterministic repair, and this is its fuel: the set of
old-name -> new-name pairs upstream itself declares.

Sources, both pinned:
  * Mathlib at tag v4.32.1 (data/raw/sources/mathlib-v4.32.1)
  * Lean core, from the v4.32.1 toolchain's own src/lean tree

Patterns, all verified against the real trees before this was written
(see NOTES.md):

  @[deprecated (since := "...")] alias OLD := NEW      -- 1537 in Mathlib
  @[deprecated (since := "...")]
  alias OLD := NEW
  @[deprecated NEW (since := "...")]                   -- replacement named in
  theorem OLD ...                                      --   the attribute

Namespaces are tracked, because `alias foo := bar` inside `namespace Nat`
means `Nat.foo -> Nat.bar`. Both the qualified and the short form are emitted;
the rewriter decides which is safe to apply.

  python3 fixers/tier1/extract_renames.py --write
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

MATHLIB_SRC = REPO_ROOT / "data" / "raw" / "sources" / "mathlib-v4.32.1"
OUT_PATH = REPO_ROOT / "fixers" / "tier1" / "renames.json"

IDENT = r"[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_'!?₀-₉α-ωΑ-Ω]*"
QUALIFIED = rf"{IDENT}(?:\.{IDENT})*"

NAMESPACE_RE = re.compile(rf"^\s*namespace\s+({QUALIFIED})")
END_RE = re.compile(rf"^\s*end(?:\s+({QUALIFIED}))?\s*$")
DEPRECATED_RE = re.compile(r"@\[\s*deprecated\b(?P<body>[^\]]*)\]")
# `alias`, optionally behind modifiers, with an optional attribute prefix.
ALIAS_RE = re.compile(
    rf"(?:^|\]\s*)(?:protected\s+|private\s+|nonrec\s+|scoped\s+)*alias\s+"
    rf"(?P<old>{QUALIFIED})\s*:=\s*(?P<new>{QUALIFIED})"
)
DECL_RE = re.compile(
    rf"^\s*(?:protected\s+|private\s+|nonrec\s+|noncomputable\s+|scoped\s+)*"
    rf"(?:theorem|lemma|def|abbrev|instance|structure|class)\s+(?P<name>{QUALIFIED})"
)
SINCE_RE = re.compile(r'since\s*:=\s*"([^"]*)"')


@dataclass
class Rename:
    old: str  # qualified where a namespace was open
    new: str
    old_short: str
    new_short: str
    since: str | None
    source: str
    kind: str  # alias | attribute


def _qualify(ns: list[str], name: str) -> str:
    """Resolve `name` against the open namespace, the way Lean would.

    Naively prefixing produced `String.trim -> String.String.trimAscii`, because
    a replacement named in the attribute is often ALREADY qualified relative to
    an enclosing namespace. So: `_root_.` means absolute, and a first component
    that matches an open namespace segment means the name is already anchored
    there and must be spliced, not prefixed.
    """
    if name.startswith("_root_."):
        return name[len("_root_.") :]
    if not ns:
        return name
    parts = name.split(".")
    if parts[0] in ns:
        i = ns.index(parts[0])
        return ".".join(ns[:i] + parts)
    return ".".join(ns) + "." + name


def _attr_replacement(body: str) -> str | None:
    """The replacement named inside `@[deprecated NEW (since := ...)]`, if any."""
    stripped = re.sub(r"\(\s*since\s*:=\s*\"[^\"]*\"\s*\)", "", body)
    stripped = re.sub(r'"[^"]*"', "", stripped).strip()  # drop message strings
    m = re.fullmatch(QUALIFIED, stripped)
    return stripped if m else None


def extract_file(path: Path, root: Path) -> list[Rename]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []

    out: list[Rename] = []
    ns: list[str] = []
    pending: tuple[str | None, str | None] | None = None  # (replacement, since)
    rel = str(path.relative_to(root))

    for raw in lines:
        line = raw.split("--")[0] if raw.lstrip().startswith("--") else raw

        m = NAMESPACE_RE.match(line)
        if m:
            ns.extend(m.group(1).split("."))
            continue
        m = END_RE.match(line)
        if m:
            if m.group(1):
                for _ in m.group(1).split("."):
                    if ns:
                        ns.pop()
            elif ns:
                ns.pop()
            continue

        dep = DEPRECATED_RE.search(line)
        if dep:
            pending = (_attr_replacement(dep.group("body")), None)
            since_m = SINCE_RE.search(dep.group("body"))
            pending = (pending[0], since_m.group(1) if since_m else None)

        # `alias OLD := NEW` -- the dominant rename form. Only trusted when a
        # deprecation attribute is attached, on this line or the one above.
        am = ALIAS_RE.search(line)
        if am and (dep or pending):
            old, new = am.group("old"), am.group("new")
            out.append(
                Rename(
                    old=_qualify(ns, old),
                    new=_qualify(ns, new),
                    old_short=old.split(".")[-1],
                    new_short=new.split(".")[-1],
                    since=pending[1] if pending else None,
                    source=rel,
                    kind="alias",
                )
            )
            pending = None
            continue

        if pending and pending[0]:
            dm = DECL_RE.match(line)
            if dm:
                old, new = dm.group("name"), pending[0]
                out.append(
                    Rename(
                        old=_qualify(ns, old),
                        new=_qualify(ns, new),
                        old_short=old.split(".")[-1],
                        new_short=new.split(".")[-1],
                        since=pending[1],
                        source=rel,
                        kind="attribute",
                    )
                )
                pending = None
                continue

        if line.strip() and not line.lstrip().startswith("@["):
            pending = None

    return out


def extract_tree(root: Path, subdir: str = "") -> list[Rename]:
    base = root / subdir if subdir else root
    if not base.exists():
        return []
    found: list[Rename] = []
    for path in sorted(base.rglob("*.lean")):
        found.extend(extract_file(path, root))
    return found


def toolchain_src() -> Path | None:
    from config import EVAL_TOOLCHAIN

    tag = EVAL_TOOLCHAIN.split(":")[-1]
    p = Path.home() / ".elan" / "toolchains" / f"leanprover--lean4---{tag}" / "src" / "lean"
    return p if p.exists() else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    if not MATHLIB_SRC.exists():
        raise SystemExit(
            f"missing {MATHLIB_SRC}\n"
            "clone it: git clone --depth 1 --branch v4.32.1 "
            "https://github.com/leanprover-community/mathlib4 "
            "data/raw/sources/mathlib-v4.32.1"
        )

    renames = extract_tree(MATHLIB_SRC, "Mathlib")
    print(f"mathlib v4.32.1 : {len(renames)} renames")

    core = toolchain_src()
    core_renames = extract_tree(core) if core else []
    print(f"lean core       : {len(core_renames)} renames")
    renames += core_renames

    # Short-name ambiguity governs what the rewriter may safely do unqualified.
    short_targets: dict[str, set[str]] = {}
    for r in renames:
        short_targets.setdefault(r.old_short, set()).add(r.new_short)
    ambiguous = {k for k, v in short_targets.items() if len(v) > 1}

    print(f"total           : {len(renames)}")
    print(f"distinct old short names: {len(short_targets)}")
    print(f"ambiguous short names   : {len(ambiguous)}  (rewriter must qualify these)")

    if args.write:
        payload = {
            "generated_from": {
                "mathlib": "v4.32.1",
                "lean_core": str(core) if core else None,
            },
            "ambiguous_short_names": sorted(ambiguous),
            "renames": [asdict(r) for r in renames],
        }
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(payload, indent=1, sort_keys=True))
        print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
