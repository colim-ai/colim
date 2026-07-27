"""Cache the Reservoir build log for every red package. Resumable."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ledger.db import DB_PATH  # noqa: E402
from reservoir_logs import cache_path, fetch, job_id  # noqa: E402


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT pkg_key, eval_build_url FROM packages "
        "WHERE final_status='RED_REGRESSION' ORDER BY pkg_key"
    ).fetchall()

    missing_url = unavailable = cached = fetched = 0
    for i, r in enumerate(rows, 1):
        jid = job_id(r["eval_build_url"])
        if not jid:
            missing_url += 1
            continue
        if cache_path(jid).exists():
            cached += 1
        elif fetch(jid) is None:
            unavailable += 1
            print(f"  UNAVAILABLE {r['pkg_key']} job={jid}", flush=True)
        else:
            fetched += 1
        if i % 50 == 0:
            print(f"  {i}/{len(rows)}", flush=True)

    print(
        f"\ndone: {fetched} fetched, {cached} already cached, "
        f"{unavailable} unavailable, {missing_url} without a build url"
    )


if __name__ == "__main__":
    main()
