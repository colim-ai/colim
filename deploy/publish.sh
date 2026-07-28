#!/usr/bin/env bash
# Regenerate the dashboard from the ledger and publish it. No sudo required.
#
#     ./deploy/publish.sh
#
# The dashboard is derived entirely from ledger/colim.sqlite, so publishing is
# always "rebuild from the current ledger" -- there is no separate content to
# keep in sync, and no way for the page to drift from the numbers.

set -euo pipefail
cd "$(dirname "$0")/.."

WEBROOT="${COLIM_WEBROOT:-/var/www/colim}"

if [[ ! -d "$WEBROOT" ]]; then
  echo "webroot $WEBROOT does not exist -- run 'sudo ./deploy/setup_site.sh <domain>' first" >&2
  exit 1
fi
if [[ ! -w "$WEBROOT" ]]; then
  echo "webroot $WEBROOT is not writable by $(whoami); re-run setup_site.sh" >&2
  exit 1
fi

echo "==> regenerating from the ledger"
python3 site/build.py
python3 site/fetch_requests.py
python3 site/build_demo.py
python3 site/build_request.py
python3 census/report.py --write >/dev/null

echo "==> checking the generated page"
python3 - <<'PY'
import sys
from pathlib import Path
html = Path("site/index.html").read_text()
# The page must remain understandable with no scripting and no external fetches.
assert "<script" not in html.lower(), "generated page contains script tags"
assert "http://" not in html.replace("http://www.w3.org", ""), "insecure external reference"
assert len(html) > 2000, "page looks truncated"
print(f"    ok ({len(html):,} bytes, no scripts, self-contained)")
PY

echo "==> publishing"
install -m 0644 site/index.html "$WEBROOT/index.html"
# Ship the audit trail alongside the page the numbers came from.
mkdir -p "$WEBROOT/census" "$WEBROOT/demo" "$WEBROOT/request"
install -m 0644 site/demo/index.html "$WEBROOT/demo/index.html"
install -m 0644 site/request/index.html "$WEBROOT/request/index.html"
install -m 0644 census/report.md "$WEBROOT/census/report.md"
install -m 0644 METHODOLOGY.md "$WEBROOT/METHODOLOGY.md"

echo "    published $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
ls -lh "$WEBROOT/index.html" | awk '{print "    "$5, $9}'
