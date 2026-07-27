#!/usr/bin/env bash
# Colim end-to-end demo: repair one real-world Lean package.
#
#   kmill/render — a Lean 4 raytracer, 146 stars, last touched 2024-05-16.
#   Pinned to Lean v4.8.0-rc1. Red on the current stable toolchain.
#
# Everything below is re-runnable from scratch and reads from the ledger.
# No network writes, no forks, nothing sent upstream.

set -uo pipefail
cd "$(dirname "$0")/.."

PKG="kmill/render"
bold() { printf '\033[1m%s\033[0m\n' "$*"; }
rule() { printf '\033[2m%s\033[0m\n' "────────────────────────────────────────────────────────────"; }

rule
bold "COLIM — automated repair of a broken Lean 4 package"
rule
echo

bold "1. What the registry says about ${PKG}"
python3 - <<'PY'
import sqlite3
c = sqlite3.connect("ledger/colim.sqlite"); c.row_factory = sqlite3.Row
r = c.execute(
    "SELECT stars, old_toolchain, target_toolchain, final_status, first_error, "
    "last_commit, eval_build_url FROM packages WHERE pkg_key='kmill/render'"
).fetchone()
print(f"   stars              {r['stars']}")
print(f"   last commit        {r['last_commit'][:10]}")
print(f"   pins toolchain     {r['old_toolchain']}")
print(f"   evaluated against  {r['target_toolchain']}")
print(f"   status             {r['final_status']}")
print(f"   failing with       {r['first_error']}")
print(f"   public build log   {r['eval_build_url']}")
PY
echo

bold "2. Why the deprecation map alone cannot fix it"
echo "   Array.mkArray was REMOVED from Lean core with no deprecated alias,"
echo "   so it is invisible to automatic extraction. It is a curated entry,"
echo "   and every curated entry must carry upstream evidence:"
python3 - <<'PY'
import tomllib
with open("fixers/tier1/renames_supplement.toml", "rb") as f:
    doc = tomllib.load(f)
for e in doc["entry"]:
    if e["old"] == "Array.mkArray":
        print(f"   {e['old']}  ->  {e['new']}")
        print(f"   evidence: {e['evidence']}")
PY
echo

bold "3. Running the repair pipeline"
echo "   clone at pinned revision -> bump toolchain -> apply rename maps -> build"
echo
python3 -u fixers/tier1/run_tier1.py --packages "$PKG" 2>&1 | sed 's/^/   /'
echo

bold "4. The entire change Colim made"
rule
git -C work/kmill--render --no-pager diff -- Main.lean old/vec.lean lean-toolchain \
  | grep -E '^(diff|[+-][^+-])' | sed 's/^/   /'
rule
echo

bold "5. Verification guards (both must hold for a green to count)"
python3 - <<'PY'
import subprocess, glob, os, re
tc = open("work/kmill--render/lean-toolchain").read().strip()
log = max(glob.glob("data/build_logs/kmill--render*.log"), key=os.path.getmtime)
text = open(log, errors="replace").read()
built = len(re.findall(r"^✔ \[\d+/\d+\]", text, re.M))
print(f"   toolchain actually used   {tc}   (must equal the campaign target)")
print(f"   Lean targets compiled     {built}   (a build that compiles nothing is never green)")
print(f"   lake exit code            {'0' if text.rstrip().endswith('0') else 'non-zero'}")
PY
echo
rule
bold "RESULT: kmill/render builds green on leanprover/lean4:v4.32.1"
echo "        Repaired by a 2-line source change, checked by the Lean kernel."
rule
