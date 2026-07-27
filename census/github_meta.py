"""Fetch the repo facts the Reservoir index does not carry: archived + last commit.

CLAUDE.md needs `archived` (excluded from K, reported separately) and
`last_commit` (never an exclusion criterion -- recorded so the report can show
the "active in the last 12 months" slice a skeptic will ask for).

metadata.json stores each source's GitHub node id, so this batches 100 repos
per GraphQL call: ~8 calls for the whole registry. Output is a timestamped
cache under data/raw/; rerunning without --refresh is a no-op.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from reservoir import REPO_ROOT, load_all

OUT = REPO_ROOT / "data" / "raw" / "github_repo_meta.json"
BATCH = 100

QUERY = """
query($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on Repository {
      id
      nameWithOwner
      isArchived
      isFork
      isPrivate
      isDisabled
      pushedAt
      stargazerCount
      licenseInfo { spdxId }
      defaultBranchRef { target { ... on Commit { committedDate oid } } }
    }
  }
}
"""


STALE_ID = "Could not resolve to a node"


def fetch_batch(ids: list[str]) -> list[dict | None]:
    """Resolve `ids` to repo nodes, tolerating ids GitHub no longer knows.

    A single stale id (repo deleted or transferred since indexing) makes the
    whole GraphQL query error out rather than returning partial data, so on
    that specific error we bisect until the offenders are isolated and mapped
    to None. Genuinely transient failures get linear backoff instead.
    """
    cmd = ["gh", "api", "graphql", "-f", f"query={QUERY}"]
    for i in ids:
        cmd += ["-f", f"ids[]={i}"]

    last = ""
    for attempt in range(5):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            return json.loads(proc.stdout)["data"]["nodes"]
        last = proc.stderr.strip()
        if STALE_ID in last:
            if len(ids) == 1:
                return [None]
            mid = len(ids) // 2
            return fetch_batch(ids[:mid]) + fetch_batch(ids[mid:])
        # 403/429 are rate limits; anything else is likely transient too.
        time.sleep(5 * (attempt + 1))
    raise SystemExit(f"gh graphql failed after retries: {last}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="refetch even if cached")
    args = ap.parse_args()

    if OUT.exists() and not args.refresh:
        cached = json.loads(OUT.read_text())
        print(f"cached {len(cached['repos'])} repos from {cached['fetched_at']}")
        print("pass --refresh to refetch")
        return

    pkgs = load_all()
    # node id -> the package keys pointing at it. Distinct packages can share a
    # source repo (aliases resolve to one dir, but forks/renames can collide).
    by_id: dict[str, list[str]] = {}
    missing: list[str] = []
    for p in pkgs:
        v = p.versions  # unused; kept explicit that ids come from metadata
        del v
        node = _node_id(p)
        if node is None:
            missing.append(p.key)
        else:
            by_id.setdefault(node, []).append(p.key)

    ids = sorted(by_id)
    repos: dict[str, dict] = {}
    unresolved: list[str] = []

    for start in range(0, len(ids), BATCH):
        chunk = ids[start : start + BATCH]
        nodes = fetch_batch(chunk)
        for node_id, node in zip(chunk, nodes):
            if not node:
                # Repo deleted, renamed away, or made private since indexing.
                unresolved.append(node_id)
                continue
            branch = (node.get("defaultBranchRef") or {}).get("target") or {}
            repos[node_id] = {
                "node_id": node_id,
                "name_with_owner": node["nameWithOwner"],
                "archived": node["isArchived"],
                "fork": node["isFork"],
                "private": node["isPrivate"],
                "disabled": node["isDisabled"],
                "pushed_at": node["pushedAt"],
                "last_commit": branch.get("committedDate"),
                "last_commit_oid": branch.get("oid"),
                "stars": node["stargazerCount"],
                "license": (node.get("licenseInfo") or {}).get("spdxId"),
            }
        print(f"  {min(start + BATCH, len(ids))}/{len(ids)}")

    payload = {
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repos": repos,
        "package_keys_by_node_id": by_id,
        "packages_without_source_id": missing,
        "unresolved_node_ids": unresolved,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True))

    print(f"\nwrote {OUT}")
    print(f"  resolved   {len(repos)}")
    print(f"  unresolved {len(unresolved)}  (deleted/renamed/private since indexing)")
    print(f"  no source id {len(missing)}")
    print(f"  archived   {sum(r['archived'] for r in repos.values())}")


def _node_id(pkg) -> str | None:
    """The GitHub node id recorded in metadata.json's first source."""
    return _SOURCE_IDS.get(pkg.key)


# reservoir.Package does not carry the raw source id, so read it back directly
# rather than widening the dataclass for a one-off fetch.
def _load_source_ids() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in (REPO_ROOT / "data" / "raw" / "reservoir-index").glob("*/*/metadata.json"):
        meta = json.loads(path.read_text())
        src = (meta.get("sources") or [{}])[0]
        if src.get("host") == "github" and src.get("id"):
            out[f"{meta['owner']}/{meta['name']}"] = src["id"]
    return out


_SOURCE_IDS = _load_source_ids()


if __name__ == "__main__":
    main()
