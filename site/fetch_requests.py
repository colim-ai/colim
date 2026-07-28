"""Snapshot open repo-requests from GitHub into site/requests.json.

The queue lives on GitHub, so the site does not need a backend to show it: this
runs at publish time and bakes the current list into static HTML. If GitHub is
unreachable the previous snapshot is kept rather than publishing an empty queue,
because "no open requests" and "we could not ask" are different claims.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "requests.json"
REPO = "colim-ai/colim"


def main() -> None:
    proc = subprocess.run(
        ["gh", "issue", "list", "--repo", REPO, "--label", "repo-request",
         "--state", "open", "--limit", "50",
         "--json", "number,title,url,createdAt"],
        capture_output=True, text=True,
    )
    if proc.returncode:
        print(f"    could not fetch requests ({proc.stderr.strip()});"
              f" keeping existing snapshot", file=sys.stderr)
        return

    issues = json.loads(proc.stdout or "[]")
    queue = [
        {
            "number": i["number"],
            # Drop our own "[request] " prefix; the list context supplies it.
            "title": i["title"].replace("[request] ", "").strip() or f"#{i['number']}",
            "url": i["url"],
            "created_at": i["createdAt"],
        }
        for i in issues
    ]
    OUT.write_text(json.dumps(queue, indent=1))
    print(f"    {len(queue)} open request(s)")


if __name__ == "__main__":
    main()
