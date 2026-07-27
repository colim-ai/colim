"""Bump a package to the campaign target: toolchain pin + Mathlib pin.

This is the first thing tier 1 does, and for many packages it is most of the
repair: the code did not change, the compiler did.

Target comes from `config.toml [campaign_target]` -- never hardcoded. Mathlib
is pinned to its RELEASE TAG for the evaluation toolchain, not to master:
mathlib master tracks the next toolchain and cannot build on v4.32.1 at all,
which is why Reservoir reports mathlib itself as red.

Both lakefile dialects are handled, plus the manifest, which is deleted so
`lake update` regenerates it consistently rather than being hand-edited into a
state Lake never produces.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config import CONFIG  # noqa: E402

TARGET = CONFIG["campaign_target"]

MATHLIB_URLS = ("leanprover-community/mathlib4", "leanprover-community/mathlib")


@dataclass
class BumpResult:
    toolchain_before: str | None = None
    toolchain_after: str | None = None
    mathlib_before: str | None = None
    mathlib_after: str | None = None
    files_changed: list[str] = field(default_factory=list)
    manifest_removed: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.files_changed) or self.manifest_removed


def bump_toolchain(root: Path, target: str, res: BumpResult) -> None:
    path = root / "lean-toolchain"
    before = path.read_text().strip() if path.exists() else None
    res.toolchain_before = before
    if before == target:
        res.toolchain_after = target
        return
    # Lean requires the trailing newline; elan is unhappy without it.
    path.write_text(target + "\n")
    res.toolchain_after = target
    res.files_changed.append("lean-toolchain")


def _bump_lakefile_lean(text: str, tag: str) -> tuple[str, str | None]:
    """`require mathlib from git "<url>" @ "<rev>"` -> pinned to `tag`."""
    found: str | None = None

    def sub(m: re.Match) -> str:
        nonlocal found
        if not any(u in m.group("url") for u in MATHLIB_URLS):
            return m.group(0)
        found = m.group("rev")
        return f'{m.group("head")}"{m.group("url")}" @ "{tag}"'

    pattern = re.compile(
        r'(?P<head>require\s+\S+\s+from\s+git\s+)"(?P<url>[^"]+)"\s*@\s*"(?P<rev>[^"]*)"'
    )
    return pattern.sub(sub, text), found


def _bump_lakefile_toml(text: str, tag: str) -> tuple[str, str | None]:
    """Pin the `[[require]]` block whose git url is Mathlib."""
    found: str | None = None
    # Split on table headers and operate on whole `[[require]]` blocks; a
    # stateful line-by-line pass is easy to get subtly wrong here.
    blocks = re.split(r"(?m)^(?=\[\[)", text)
    out = []
    for block in blocks:
        if block.strip().startswith("[[require]]") and any(u in block for u in MATHLIB_URLS):
            m = re.search(r'(?m)^\s*rev\s*=\s*"([^"]*)"', block)
            if m:
                found = m.group(1)
                block = re.sub(
                    r'(?m)^(\s*rev\s*=\s*)"[^"]*"', rf'\g<1>"{tag}"', block
                )
            else:
                block = block.rstrip("\n") + f'\nrev = "{tag}"\n'
        out.append(block)
    return "".join(out), found


def bump_mathlib(root: Path, tag: str, res: BumpResult) -> None:
    for name, fn in (
        ("lakefile.lean", _bump_lakefile_lean),
        ("lakefile.toml", _bump_lakefile_toml),
    ):
        path = root / name
        if not path.exists():
            continue
        original = path.read_text(errors="replace")
        updated, before = fn(original, tag)
        if before is not None:
            res.mathlib_before = before
        if updated != original:
            path.write_text(updated)
            res.files_changed.append(name)
            res.mathlib_after = tag


def drop_manifest(root: Path, res: BumpResult) -> None:
    """Remove lake-manifest.json so `lake update` regenerates it.

    Hand-editing a manifest produces states Lake never generates; letting Lake
    rebuild it keeps the dependency closure internally consistent.
    """
    path = root / "lake-manifest.json"
    if path.exists():
        path.unlink()
        res.manifest_removed = True


def bump(root: Path, *, toolchain: str | None = None, mathlib_tag: str | None = None,
         mathlib_downstream: bool = True) -> BumpResult:
    res = BumpResult()
    bump_toolchain(root, toolchain or TARGET["toolchain"], res)
    if mathlib_downstream:
        bump_mathlib(root, mathlib_tag or TARGET["mathlib_tag"], res)
    if res.changed:
        drop_manifest(root, res)
    return res


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--no-mathlib", action="store_true")
    args = ap.parse_args()

    result = bump(Path(args.directory), mathlib_downstream=not args.no_mathlib)
    print(json.dumps(result.__dict__, indent=2, default=str))
