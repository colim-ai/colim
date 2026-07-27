"""Tier-1c: module rename/split map, from git rename detection.

The declaration rename map cannot express `bad import
'Mathlib.MeasureTheory.Integral.Bochner'` -- a MODULE moved. Upstream
reorganises module paths constantly, so this is a large mechanical class.

Two shapes, both derived by diffing the package's OLD Mathlib revision against
the campaign target:

  rename  `R` in git's rename detection: one file moved to one new path.
  split   the file was deleted and a DIRECTORY of the same name appeared.
          `Integral/Bochner.lean` -> `Integral/Bochner/{Basic,...}.lean`.
          Git reports this as D + A, not R, so it needs handling of its own.

For a split we emit ALL the children. Importing the superset is the
highest-probability repair, and guessing wrong costs a red build, never a false
green -- verification arbitrates, exactly as ruled.

  python3 fixers/tier1/module_renames.py --old-rev <sha>
  python3 fixers/tier1/module_renames.py --for-package owner/name
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for p in (REPO_ROOT, REPO_ROOT / "census"):
    sys.path.insert(0, str(p))

MATHLIB_SRC = REPO_ROOT / "data" / "raw" / "sources" / "mathlib-v4.32.1"
CACHE_DIR = REPO_ROOT / "data" / "raw" / "module_renames"


def module_of(path: str) -> str | None:
    """`Mathlib/Foo/Bar.lean` -> `Mathlib.Foo.Bar`."""
    if not path.endswith(".lean"):
        return None
    return path[: -len(".lean")].replace("/", ".")


# Mathlib diffs are far larger than git's default rename limit. Exceeding it
# makes git SKIP rename detection entirely and say so only on stderr -- the
# first run reported 0 renames instead of 216, which looks like a valid answer.
RENAME_LIMIT = 30000
SKIPPED_WARNING = "rename detection was skipped"


def _git(args: list[str], cwd: Path, *, check_rename_warning: bool = False) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace"
    )
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    if check_rename_warning and SKIPPED_WARNING in proc.stderr:
        # Never accept a silently-degraded map.
        raise RuntimeError(
            f"git skipped rename detection (limit too low):\n{proc.stderr.strip()}\n"
            f"raise RENAME_LIMIT above {RENAME_LIMIT} and rerun"
        )
    return proc.stdout


def ensure_rev(rev: str, src: Path = MATHLIB_SRC) -> None:
    """Make `rev` available locally. Rename detection compares TREES, so a
    shallow fetch of the single commit is enough -- no history needed."""
    try:
        _git(["cat-file", "-e", f"{rev}^{{commit}}"], src)
        return
    except RuntimeError:
        pass
    _git(["fetch", "--depth", "1", "origin", rev], src)


def build_map(old_rev: str, src: Path = MATHLIB_SRC, subdir: str = "Mathlib") -> dict:
    """old module -> [candidate new modules], for one old revision."""
    ensure_rev(old_rev, src)
    # -M only. Adding -C (copy detection) pushes the comparison past the limit
    # and git then skips detection altogether: 0 renames instead of 216.
    raw = _git(
        [
            "-c",
            f"diff.renameLimit={RENAME_LIMIT}",
            "diff",
            "--name-status",
            "-M",
            old_rev,
            "HEAD",
            "--",
            f"{subdir}/",
        ],
        src,
        check_rename_warning=True,
    )

    renames: dict[str, list[str]] = {}
    deleted: list[str] = []
    added: list[str] = []

    for line in raw.splitlines():
        parts = line.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")) and len(parts) >= 3:
            old_m, new_m = module_of(parts[1]), module_of(parts[2])
            if old_m and new_m and old_m != new_m:
                renames.setdefault(old_m, []).append(new_m)
        elif status == "D" and len(parts) >= 2:
            m = module_of(parts[1])
            if m:
                deleted.append(m)
        elif status == "A" and len(parts) >= 2:
            m = module_of(parts[1])
            if m:
                added.append(m)

    # Splits: a deleted module whose name became a namespace of new modules.
    splits: dict[str, list[str]] = {}
    for old_m in deleted:
        children = sorted(a for a in added if a.startswith(old_m + "."))
        if children:
            # `.Basic` is Mathlib's convention for the core of a split module,
            # so it goes first; the rest follow to keep the superset available.
            children.sort(key=lambda c: (not c.endswith(".Basic"), c))
            splits[old_m] = children

    combined = {k: v for k, v in renames.items()}
    for k, v in splits.items():
        combined.setdefault(k, v)

    return {
        "old_rev": old_rev,
        "subdir": subdir,
        "renames": renames,
        "splits": splits,
        "map": combined,
    }


def cached_map(old_rev: str, src: Path = MATHLIB_SRC, subdir: str = "Mathlib") -> dict:
    """Many packages pin the same Mathlib revision; compute each one once."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{subdir.lower()}-{old_rev}.json"
    if path.exists():
        return json.loads(path.read_text())
    doc = build_map(old_rev, src, subdir)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True))
    return doc


def mathlib_rev_for(pkg_key: str) -> str | None:
    """The Mathlib revision the package was last indexed against."""
    from reservoir import load_all, normalize_repo

    for pkg in load_all():
        if pkg.key.lower() != pkg_key.lower():
            continue
        v = pkg.latest_version
        if not v:
            return None
        for d in v.dependencies:
            if normalize_repo(d.get("url")) in {
                "leanprover-community/mathlib4",
                "leanprover-community/mathlib",
            }:
                return d.get("rev")
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-rev")
    ap.add_argument("--for-package")
    ap.add_argument("--show", type=int, default=10)
    args = ap.parse_args()

    rev = args.old_rev
    if args.for_package:
        rev = mathlib_rev_for(args.for_package)
        if not rev:
            raise SystemExit(f"no mathlib revision recorded for {args.for_package}")
        print(f"{args.for_package}: mathlib {rev}")
    if not rev:
        raise SystemExit("pass --old-rev or --for-package")

    doc = cached_map(rev)
    print(
        f"{len(doc['renames'])} module rename(s), {len(doc['splits'])} split(s), "
        f"{len(doc['map'])} mapped module(s)"
    )
    for old, new in list(doc["map"].items())[: args.show]:
        arrow = new[0] if len(new) == 1 else f"{len(new)} modules: {', '.join(new[:3])}..."
        print(f"  {old}\n    -> {arrow}")


if __name__ == "__main__":
    main()
