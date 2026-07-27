"""Day-1 census: Reservoir snapshot + GitHub metadata -> ledger rows.

Applies the CLAUDE.md definitions literally. The only judgement calls made here
are documented inline and surfaced in the report; everything else is mechanical.

Read-only against the network -- consumes the pinned snapshot and the cached
GitHub fetch. Rerunnable: upserts by pkg_key, never double-counts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import EVAL_TOOLCHAIN, INDEX_COMMIT, CENSUS  # noqa: E402
from ledger.db import connect, replace_observations, set_meta, upsert_package  # noqa: E402
from reservoir import REPO_ROOT, load_all, snapshot_commit, version_key  # noqa: E402

GH_META = REPO_ROOT / "data" / "raw" / "github_repo_meta.json"

EVAL_KEY = version_key(EVAL_TOOLCHAIN)

AHEAD_REASON = (
    "pins a toolchain newer than the evaluation toolchain; Reservoir forces the "
    "toolchain, so this red is a forced-BACKWARDS failure, which is not a regression"
)


def classify(pkg, gh: dict | None) -> tuple[str, str]:
    """Return (final_status, status_reason) under the load-bearing definitions.

    RED_REGRESSION requires BOTH: red on the eval toolchain, AND at least one
    recorded green earlier. Never-green packages are NEVER_GREEN and stay out of
    the headline. Insufficient data is UNKNOWN. These are never conflated.
    """
    outcome = pkg.outcome_on(EVAL_TOOLCHAIN)

    if outcome is None:
        return "UNKNOWN", f"no recorded outcome on {EVAL_TOOLCHAIN}"
    if outcome:
        return "GREEN_CURRENT", ""

    # Reservoir forces the toolchain (NOTES.md). A package that has already moved
    # PAST the evaluation toolchain therefore fails for the opposite reason to a
    # regression: its source is too new for the compiler it was forced onto.
    # mathlib and batteries both land here on v4.33.0-rc1. Counting them in M
    # would not survive a hostile audit, so they go to UNKNOWN -- the honest
    # answer, since we cannot tell from this data whether they are also broken.
    pinned = pkg.pinned_toolchain
    if pinned and version_key(pinned) > EVAL_KEY:
        return "UNKNOWN", AHEAD_REASON

    build = pkg.build_on(EVAL_TOOLCHAIN)
    if pkg.ever_green_before(build.run_at):
        return "RED_REGRESSION", ""
    return "NEVER_GREEN", "no successful build recorded on any earlier toolchain"


def last_green(pkg) -> tuple[str | None, str | None]:
    """Newest toolchain with a recorded green, by version order."""
    greens = [b for b in pkg.builds if b.built]
    if not greens:
        return None, None
    best = max(greens, key=lambda b: (version_key(b.toolchain), b.run_at))
    return best.toolchain, best.run_at


def main() -> None:
    snapshot = snapshot_commit()
    if snapshot != INDEX_COMMIT:
        raise SystemExit(
            f"snapshot on disk is {snapshot} but config pins {INDEX_COMMIT}.\n"
            "The pinned commit is load-bearing for every census number. "
            "Update config.toml deliberately, or re-clone the pinned commit."
        )

    if not GH_META.exists():
        raise SystemExit(f"missing {GH_META} -- run census/github_meta.py first")

    gh_doc = json.loads(GH_META.read_text())
    repos = gh_doc["repos"]
    # node id -> package keys; invert so each package finds its repo facts.
    gh_by_pkg: dict[str, dict] = {}
    for node_id, keys in gh_doc["package_keys_by_node_id"].items():
        for k in keys:
            if node_id in repos:
                gh_by_pkg[k] = repos[node_id]

    pkgs = load_all()
    conn = connect()

    for pkg in pkgs:
        gh = gh_by_pkg.get(pkg.key)
        archived = gh["archived"] if gh else None
        source_available = gh is not None

        status, reason = classify(pkg, gh)

        # K = all indexed, NON-ARCHIVED packages. Archived are counted and
        # reported separately. Vanished sources stay in K: the rule excludes
        # archived, and a deleted repo is not evidence of archival. Flagged via
        # source_available so the report can show the sensitivity either way.
        in_k = not bool(archived)
        if archived:
            reason = "archived: excluded from K, reported separately"
        elif not source_available:
            reason = (reason + "; " if reason else "") + (
                "source repo no longer resolves on GitHub (deleted/private/transferred)"
            )

        build = pkg.build_on(EVAL_TOOLCHAIN)
        lg_tc, lg_at = last_green(pkg)
        downstream = pkg.depends_on_mathlib()

        upsert_package(
            conn,
            {
                "pkg_key": pkg.key,
                "owner": pkg.owner,
                "name": pkg.name,
                "repo": gh["name_with_owner"] if gh else pkg.source_full_name,
                "repo_url": pkg.repo_url,
                "default_branch": pkg.default_branch,
                "stars": gh["stars"] if gh else pkg.stars,
                "license": (gh["license"] if gh else None) or pkg.license,
                "archived": None if archived is None else int(archived),
                "source_available": int(source_available),
                "is_fork": int(gh["fork"]) if gh else None,
                "last_commit": gh["last_commit"] if gh else None,
                "in_k": int(in_k),
                "release_train": int(pkg.is_release_train),
                "mathlib_downstream": None if downstream is None else int(downstream),
                "old_toolchain": pkg.pinned_toolchain,
                "target_toolchain": EVAL_TOOLCHAIN,
                "eval_revision": build.revision if build else None,
                "eval_build_url": build.url if build else None,
                "eval_run_at": build.run_at if build else None,
                "last_green_toolchain": lg_tc,
                "last_green_run_at": lg_at,
                # Populated Day 2 from real logs; never guessed.
                "failure_origin": "unknown" if status == "RED_REGRESSION" else None,
                "final_status": status,
                "status_reason": reason,
            },
        )

        replace_observations(
            conn,
            pkg.key,
            [
                {
                    "toolchain": b.toolchain,
                    "revision": b.revision,
                    "built": int(b.built),
                    "run_at": b.run_at,
                    "url": b.url,
                }
                for b in pkg.builds
            ],
        )

    set_meta(conn, "index_commit", snapshot)
    set_meta(conn, "index_committed_at", CENSUS["index_committed_at"])
    set_meta(conn, "eval_toolchain", EVAL_TOOLCHAIN)
    set_meta(conn, "eval_toolchain_coverage", str(CENSUS["eval_toolchain_coverage"]))
    set_meta(conn, "github_meta_fetched_at", gh_doc["fetched_at"])
    set_meta(conn, "census_packages_indexed", str(len(pkgs)))
    conn.commit()

    n = conn.execute("SELECT COUNT(*) c FROM packages").fetchone()["c"]
    obs = conn.execute("SELECT COUNT(*) c FROM build_observations").fetchone()["c"]
    print(f"ledger: {n} packages, {obs} build observations")
    print("run census/report.py for the census numbers")


if __name__ == "__main__":
    main()
