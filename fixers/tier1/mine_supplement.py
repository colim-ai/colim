"""Mine PROPOSED supplement entries for identifiers the maps cannot resolve.

Modelled on how `Array.mkArray -> Array.replicate` was found: an identifier the
compiler rejects, absent from the deprecation map, was renamed upstream at some
point and the alias did not survive. The commit that did it is the evidence.

Method, per unresolved identifier:
  1. `git log -S <ident>` over the upstream tree -- content search, so it finds
     the commit where the name stopped appearing, not merely commits that
     mention it in a message.
  2. Read the commit for a replacement name (rename/replace phrasing, or a
     same-commit addition of a similar name).
  3. Emit a PROPOSED entry carrying the commit URL as evidence.

Nothing here writes an ACTIVE entry. Proposals promote only after verification
-- a fixture test or a green rebuild that uses them -- exactly as ruled. And
`hard_removal` splits at this point into:
    recoverable_rename  a replacement was identified
    true_removal        the declaration went away with no successor

  python3 fixers/tier1/mine_supplement.py --limit 40
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
for p in (REPO_ROOT, REPO_ROOT / "harness", HERE):
    sys.path.insert(0, str(p))

from ledger.db import connect  # noqa: E402
from rewrite import load_map  # noqa: E402

MATHLIB_SRC = REPO_ROOT / "data" / "raw" / "sources" / "mathlib-v4.32.1"
PROPOSALS_PATH = HERE / "renames_supplement_proposed.json"

MATHLIB_COMMIT_URL = "https://github.com/leanprover-community/mathlib4/commit/{}"

RENAME_PHRASES = re.compile(
    r"(?:rename[sd]?|replace[sd]?|deprecate[sd]?)\s+`?([\w.']+)`?\s+"
    r"(?:to|with|by|->|→)\s+`?([\w.']+)`?",
    re.I,
)


def unresolved_identifiers(conn, qualified: dict, short: dict) -> Counter:
    """Identifiers named in reds' logs that neither map can resolve."""
    from errors import UNKNOWN_IDENT_RE
    from reservoir_logs import fetch, job_id, strip_timestamps

    counts: Counter = Counter()
    rows = conn.execute(
        "SELECT pkg_key, eval_build_url FROM packages WHERE in_k=1 "
        "AND final_status='RED_REGRESSION' AND error_classes LIKE '%unknown_identifier%'"
    ).fetchall()

    for r in rows:
        jid = job_id(r["eval_build_url"])
        text = fetch(jid) if jid else None
        if not text:
            continue
        for name in UNKNOWN_IDENT_RE.findall("\n".join(strip_timestamps(text))):
            if name in qualified or name.split(".")[-1] in short:
                continue
            counts[name] += 1
    return counts


def has_history(src: Path = MATHLIB_SRC) -> bool:
    return not (src / ".git" / "shallow").exists()


def find_removing_commit(ident: str, src: Path = MATHLIB_SRC) -> dict | None:
    """The most recent commit that changed occurrences of `ident`."""
    short_name = ident.split(".")[-1]
    proc = subprocess.run(
        [
            "git", "log", "-S", short_name, "--pickaxe-regex" if False else "--no-merges",
            "-n", "1", "--format=%H%x00%s%x00%cI", "--", "Mathlib/",
        ],
        cwd=src,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=180,
    )
    if proc.returncode or not proc.stdout.strip():
        return None
    sha, subject, date = (proc.stdout.strip().split("\0") + ["", ""])[:3]
    return {"sha": sha, "subject": subject, "date": date}


def replacement_from(subject: str, ident: str) -> str | None:
    short_name = ident.split(".")[-1]
    for old, new in RENAME_PHRASES.findall(subject):
        if old.split(".")[-1] == short_name:
            return new
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    if not has_history():
        raise SystemExit(
            f"{MATHLIB_SRC} is a shallow clone; `git log -S` needs history.\n"
            "run: git -C data/raw/sources/mathlib-v4.32.1 fetch --unshallow origin"
        )

    conn = connect()
    qualified, short = load_map()
    counts = unresolved_identifiers(conn, qualified, short)
    print(f"{len(counts)} distinct unresolved identifier(s) across the red set\n")

    proposals = []
    recoverable = removal = unknown = 0

    for ident, hits in counts.most_common(args.limit):
        commit = find_removing_commit(ident)
        if not commit:
            unknown += 1
            print(f"  {ident:<45} no commit found")
            continue

        new = replacement_from(commit["subject"], ident)
        kind = "rename" if new else "hard_removal"
        resolution = "recoverable_rename" if new else "true_removal"
        recoverable += int(bool(new))
        removal += int(not new)

        proposals.append(
            {
                "old": ident,
                "new": new,
                "kind": kind,
                "resolution": resolution,
                "evidence": MATHLIB_COMMIT_URL.format(commit["sha"]),
                "evidence_subject": commit["subject"],
                "evidence_date": commit["date"],
                "source": "agent",
                "status": "proposed",
                "seen_in_red_packages": hits,
            }
        )
        print(f"  {ident:<45} {resolution:<20} {commit['subject'][:60]}")

    PROPOSALS_PATH.write_text(json.dumps({"proposals": proposals}, indent=1, sort_keys=True))
    print(
        f"\nwrote {PROPOSALS_PATH}\n"
        f"  recoverable_rename {recoverable}\n"
        f"  true_removal       {removal}\n"
        f"  no evidence found  {unknown}\n"
        "\nAll entries are PROPOSED. None is active until a fixture test or a "
        "green rebuild verifies it."
    )


if __name__ == "__main__":
    main()
