"""Fetch and cache Reservoir's public build logs.

Reservoir records a GitHub Actions job URL for every build entry. Those logs are
public, so the red state of every package is one click from evidence -- this
module just makes them local, hashed, and cheap to re-read.

Logs are cached gzipped under data/raw/logs/. Refetching is a no-op.
"""

from __future__ import annotations

import gzip
import hashlib
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "data" / "raw" / "logs"
RESERVOIR_REPO = "leanprover/reservoir"

JOB_RE = re.compile(r"/job/(\d+)")


def job_id(build_url: str | None) -> str | None:
    """Extract the Actions job id from a Reservoir build URL."""
    if not build_url:
        return None
    m = JOB_RE.search(build_url)
    return m.group(1) if m else None


def cache_path(jid: str) -> Path:
    return LOG_DIR / f"{jid}.log.gz"


def fetch(jid: str, *, refresh: bool = False) -> str | None:
    """Return the job log, from cache when present.

    None means the log is genuinely unavailable -- Actions retention has
    expired, or the job was removed. That is recorded, never guessed around.
    """
    path = cache_path(jid)
    if path.exists() and not refresh:
        with gzip.open(path, "rt", errors="replace") as f:
            return f.read()

    proc = subprocess.run(
        ["gh", "api", f"repos/{RESERVOIR_REPO}/actions/jobs/{jid}/logs"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if proc.returncode != 0:
        return None

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as f:
        f.write(proc.stdout)
    return proc.stdout


def log_hash(text: str) -> str:
    """sha256 of the raw log, stored in the ledger as red_state_log_hash."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


TIMESTAMP_RE = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z\s?")


def strip_timestamps(text: str) -> list[str]:
    """Actions prefixes every line with an ISO timestamp; drop it."""
    return [TIMESTAMP_RE.sub("", line) for line in text.splitlines()]
