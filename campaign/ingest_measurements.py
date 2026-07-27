"""Ingest measure-reds run results back into the ledger.

Reclassification rules (ruling 2026-07-27), applied mechanically:

  real code errors  -> stays in M, error classes recorded, red_basis=measured_actions
  builds green      -> leaves M (final_status GREEN_CURRENT), delta logged
  still infra       -> stays in M, remains flagged as infra_uninformative

Every row gets `measurement_run_url` so the outcome is one click from a public
Actions log in our own repository.

  python3 campaign/ingest_measurements.py <run-id>
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "harness"))

sys.path.insert(0, str(HERE.parent / "census"))

from classify_reds import dep_names_for  # noqa: E402
from config import gh_org  # noqa: E402
from errors import INFRA_CLASSES, classify_log  # noqa: E402
from ledger.db import connect, now  # noqa: E402
from reservoir import load_all  # noqa: E402
from reservoir_logs import log_hash, strip_timestamps  # noqa: E402


def download_artifacts(run_id: str, repo: str, dest: Path) -> list[Path]:
    proc = subprocess.run(
        ["gh", "run", "download", run_id, "--repo", repo, "--dir", str(dest)],
        capture_output=True,
        text=True,
    )
    if proc.returncode:
        raise SystemExit(f"gh run download failed: {proc.stderr.strip()}")
    for z in dest.rglob("*.zip"):
        with zipfile.ZipFile(z) as zf:
            zf.extractall(z.parent)
    return sorted(dest.rglob("outcome.txt"))


def parse_outcome(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    args = ap.parse_args()

    repo = f"{gh_org()}/colim"
    run_url = f"https://github.com/{repo}/actions/runs/{args.run_id}"
    conn = connect()

    pkgs = {p.key: p for p in load_all()}

    tmp = Path(tempfile.mkdtemp(prefix="colim-measure-"))
    try:
        outcomes = download_artifacts(args.run_id, repo, tmp)
        if not outcomes:
            raise SystemExit("no outcome.txt artifacts found in that run")

        moved_out = stayed = still_infra = 0
        for path in outcomes:
            meta = parse_outcome(path)
            pkg = meta.get("pkg_key")
            if not pkg:
                continue
            log_file = path.parent / "build.log"
            text = log_file.read_text(errors="replace") if log_file.exists() else ""
            # Re-classify HERE rather than trusting the runner's failure_origin:
            # the Actions job has no dependency list, so it attributes an error
            # in `Mathlib/...` to `self`. Attribution needs the package's
            # declared dependencies, which only the index gives us.
            verdict = classify_log(strip_timestamps(text), dep_names_for(pkgs.get(pkg)))
            classes = set(verdict.error_classes)

            # `verified_green` from the shared verifier is authoritative: it
            # already required that something actually compiled. The step
            # outcome alone would let a vacuous green through, which is exactly
            # the failure this guard exists to prevent.
            vpath = path.parent / "verdict.json"
            if vpath.exists():
                v = json.loads(vpath.read_text())
                green = bool(v.get("verified_green"))
                classes = set(v.get("error_classes") or classes)
                targets = v.get("targets") or []
                if green and not targets:
                    green = False  # cannot be green having built no targets
            else:
                # No verdict emitted -- refuse to infer green from exit status.
                green = False
                print(f"  {pkg}: no verdict.json; not treated as green")

            if green:
                # Measured healthy: it leaves M. This is a real correction to
                # the headline, so the delta is logged, not silently applied.
                moved_out += 1
                conn.execute(
                    "UPDATE packages SET final_status='GREEN_CURRENT', "
                    "red_basis='measured_actions', status_reason=?, "
                    "measurement_run_url=?, error_classes=?, updated_at=? "
                    "WHERE pkg_key=?",
                    (
                        "measured green on the evaluation toolchain in our own Actions "
                        "run; Reservoir's red was a cache-fetch failure",
                        run_url,
                        json.dumps(sorted(classes)),
                        now(),
                        pkg,
                    ),
                )
            elif classes - INFRA_CLASSES:
                stayed += 1
                conn.execute(
                    "UPDATE packages SET red_basis='measured_actions', "
                    "error_classes=?, failure_origin=?, red_state_log_hash=?, "
                    "first_error=?, measurement_run_url=?, updated_at=? WHERE pkg_key=?",
                    (
                        json.dumps(sorted(classes)),
                        verdict.failure_origin,
                        log_hash(text),
                        verdict.first_error,
                        run_url,
                        now(),
                        pkg,
                    ),
                )
            else:
                still_infra += 1
                conn.execute(
                    "UPDATE packages SET measurement_run_url=?, updated_at=? "
                    "WHERE pkg_key=?",
                    (run_url, now(), pkg),
                )
        conn.commit()

        print(f"ingested {len(outcomes)} measurements from {run_url}")
        print(f"  moved OUT of M (measured green) : {moved_out}")
        print(f"  stayed in M (real code errors)  : {stayed}")
        print(f"  still infrastructure, reflagged : {still_infra}")
        if moved_out:
            print("\nM changed. Log the delta in census/NOTES.md and regenerate report.md.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
