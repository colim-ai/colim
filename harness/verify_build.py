"""Build a checked-out package and emit a machine-readable verdict.

This is the single entry point used by the measure-reds GitHub Actions workflow
and by Day-6 fix verification, so both go through exactly the same vacuous-green
guard as local reproduction. A build that compiles nothing can never count as
green through any route.

  python3 harness/verify_build.py <dir> --toolchain <tc> [--mathlib] [--json out.json]

Exit code is 0 only for a VERIFIED green: lake succeeded and at least one Lean
target was actually compiled.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build import (  # noqa: E402
    BUILT_SOMETHING_RE,
    NO_TARGET_RE,
    declared_targets,
    lake_build_verified,
    _run,
)
from errors import classify_log  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--toolchain", required=True)
    ap.add_argument("--mathlib", action="store_true", help="run `lake exe cache get` first")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--log", dest="log_out", default="build.log")
    ap.add_argument("--timeout", type=int, default=5400)
    args = ap.parse_args()

    dest = Path(args.directory).resolve()
    if not dest.is_dir():
        raise SystemExit(f"not a directory: {dest}")

    env = os.environ.copy()
    # Reservoir forces the toolchain rather than honouring lean-toolchain; any
    # measurement that does not do the same is not comparable to its rows.
    env["ELAN_TOOLCHAIN"] = args.toolchain

    log: list[str] = [
        f"# colim verify_build",
        f"# dir       {dest}",
        f"# toolchain {args.toolchain}  (FORCED)",
        "",
    ]

    if args.mathlib:
        code, _ = _run(["lake", "exe", "cache", "get"], dest, env, args.timeout, log)
        if code:
            log.append("##[note] cache get failed; continuing to build uncached")

    code, timed_out, targets = lake_build_verified(dest, env, args.timeout, log)

    text = "\n".join(log)
    Path(args.log_out).write_text(text)

    built_nothing = bool(NO_TARGET_RE.search(text)) or not BUILT_SOMETHING_RE.search(text)
    verified_green = code == 0 and not built_nothing
    verdict = classify_log(text.splitlines(), set())

    result = {
        "exit_code": code,
        "timed_out": timed_out,
        "verified_green": verified_green,
        "built_nothing": built_nothing,
        "targets": targets or declared_targets(dest),
        "error_classes": verdict.error_classes,
        "failure_origin": verdict.failure_origin,
        "first_error": verdict.first_error,
    }

    payload = json.dumps(result, indent=2)
    if args.json_out:
        Path(args.json_out).write_text(payload)
    print(payload)

    # Exit 0 ONLY on a verified green.
    sys.exit(0 if verified_green else 1)


if __name__ == "__main__":
    main()
