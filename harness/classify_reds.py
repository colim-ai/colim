"""Day-2: classify every red package's Reservoir log into the ledger.

Writes error_classes, failure_origin and red_state_log_hash. Idempotent --
rerunning reclassifies from the cached logs without refetching.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "census"))

from errors import classify_log  # noqa: E402
from ledger.db import connect, now  # noqa: E402
from reservoir import load_all  # noqa: E402
from reservoir_logs import fetch, job_id, log_hash, strip_timestamps  # noqa: E402


def dep_names_for(pkg) -> set[str]:
    """Declared dependency names for the latest indexed revision, lowercased."""
    v = pkg.latest_version
    if not v:
        return set()
    return {(d.get("name") or "").lower() for d in v.dependencies if d.get("name")}


def main() -> None:
    conn = connect()
    pkgs = {p.key: p for p in load_all()}

    rows = conn.execute(
        "SELECT pkg_key, eval_build_url FROM packages "
        "WHERE final_status='RED_REGRESSION' ORDER BY pkg_key"
    ).fetchall()

    no_log = 0
    for i, r in enumerate(rows, 1):
        jid = job_id(r["eval_build_url"])
        text = fetch(jid) if jid else None
        if text is None:
            no_log += 1
            conn.execute(
                "UPDATE packages SET failure_origin='unknown', "
                "error_classes=?, updated_at=? WHERE pkg_key=?",
                (json.dumps(["log_unavailable"]), now(), r["pkg_key"]),
            )
            continue

        pkg = pkgs.get(r["pkg_key"])
        verdict = classify_log(strip_timestamps(text), dep_names_for(pkg) if pkg else set())

        # infra_only: nothing but runner-infrastructure failures, so the log says
        # nothing about whether this package's code survives the toolchain.
        infra_only = bool(verdict.error_classes) and all(
            c.startswith("infra_") for c in verdict.error_classes
        )

        conn.execute(
            "UPDATE packages SET error_classes=?, failure_origin=?, "
            "red_state_log_hash=?, first_error=?, error_file_count=?, "
            "error_files=?, infra_only=?, updated_at=? WHERE pkg_key=?",
            (
                json.dumps(verdict.error_classes),
                verdict.failure_origin,
                log_hash(text),
                verdict.first_error,
                len(verdict.error_files),
                json.dumps(verdict.error_files),
                int(infra_only),
                now(),
                r["pkg_key"],
            ),
        )
        if i % 100 == 0:
            conn.commit()
            print(f"  {i}/{len(rows)}", flush=True)

    conn.commit()
    print(f"classified {len(rows) - no_log} logs; {no_log} unavailable")


if __name__ == "__main__":
    main()
