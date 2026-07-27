"""Generate the static dashboard FROM the ledger. No independent data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from ledger.db import connect, get_meta  # noqa: E402

OUT = HERE / "index.html"

CSS = """
:root { --bg:#0d1117; --fg:#e6edf3; --dim:#8b949e; --line:#30363d;
        --red:#f85149; --green:#3fb950; --amber:#d29922; --accent:#58a6ff; }
@media (prefers-color-scheme: light) {
  :root { --bg:#fff; --fg:#1f2328; --dim:#59636e; --line:#d1d9e0;
          --red:#cf222e; --green:#1a7f37; --amber:#9a6700; --accent:#0969da; }
}
* { box-sizing:border-box; }
body { margin:0; padding:2.5rem 1.5rem; background:var(--bg); color:var(--fg);
       font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }
.wrap { max-width:960px; margin:0 auto; }
h1 { font-size:1.9rem; margin:0 0 .25rem; letter-spacing:-.02em; }
.sub { color:var(--dim); margin:0 0 2rem; font-size:.95rem; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1rem; margin-bottom:1rem; }
.kpi { border:1px solid var(--line); border-radius:10px; padding:1rem 1.15rem; }
.kpi .n { font-size:2.1rem; font-weight:650; letter-spacing:-.03em; }
.kpi .l { color:var(--dim); font-size:.8rem; text-transform:uppercase; letter-spacing:.06em; }
.note { border-left:3px solid var(--amber); padding:.6rem 1rem; margin:1.25rem 0;
        background:color-mix(in srgb, var(--amber) 8%, transparent); border-radius:0 6px 6px 0;
        font-size:.9rem; }
h2 { font-size:1.05rem; margin:2.25rem 0 .75rem; text-transform:uppercase;
     letter-spacing:.07em; color:var(--dim); }
table { border-collapse:collapse; width:100%; font-size:.9rem; }
th,td { text-align:left; padding:.5rem .6rem; border-bottom:1px solid var(--line); }
th { color:var(--dim); font-weight:600; font-size:.78rem; text-transform:uppercase; letter-spacing:.05em; }
td.n, th.n { text-align:right; font-variant-numeric:tabular-nums; }
.bar { height:7px; border-radius:4px; background:var(--line); overflow:hidden; }
.bar > i { display:block; height:100%; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.85em; }
a { color:var(--accent); }
.red{color:var(--red)} .green{color:var(--green)} .amber{color:var(--amber)}
footer { margin-top:3rem; padding-top:1rem; border-top:1px solid var(--line);
         color:var(--dim); font-size:.82rem; }
.scroll { overflow-x:auto; }
"""


def main() -> None:
    conn = connect()
    one = lambda q, *a: conn.execute(q, a).fetchone()[0]  # noqa: E731
    rows = lambda q, *a: conn.execute(q, a).fetchall()  # noqa: E731

    k = one("SELECT COUNT(*) FROM packages WHERE in_k=1")
    m = one("SELECT COUNT(*) FROM packages WHERE in_k=1 AND final_status='RED_REGRESSION'")
    km = one("SELECT COUNT(*) FROM packages WHERE in_k=1 AND mathlib_downstream=1")
    uninf = one(
        "SELECT COUNT(*) FROM packages WHERE in_k=1 AND final_status='RED_REGRESSION' "
        "AND red_basis='infra_uninformative'"
    )
    repaired = one(
        "SELECT COUNT(*) FROM packages WHERE tier1_result IS NOT NULL "
        "AND json_extract(tier1_result,'$.green')=1"
    )
    eval_tc = get_meta(conn, "eval_toolchain")

    h = [
        "<!doctype html><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        "<title>Colim — Lean ecosystem health</title>",
        f"<style>{CSS}</style><div class=wrap>",
        "<h1>Colim</h1>",
        f"<p class=sub>Automated repair of formal mathematics. Census of the Lean "
        f"package registry on <code>{eval_tc}</code>.</p>",
        "<div class=kpis>",
        f"<div class=kpi><div class=n>{k}</div><div class=l>K · packages in scope</div></div>",
        f"<div class=kpi><div class='n red'>{m}</div><div class=l>M · red regressions</div></div>",
        f"<div class=kpi><div class=n>{m / k:.0%}</div><div class=l>M / K</div></div>",
        f"<div class=kpi><div class='n green'>{repaired}</div><div class=l>N · repaired &amp; verified</div></div>",
        "</div>",
        f"<div class=note><strong>Quote this alongside M.</strong> {uninf} of the {m} reds "
        "are <code>infra_uninformative</code> — the registry's build failed on a cache fetch, "
        "so its log cannot say whether the package's own code survives. They are being "
        "resolved by measurement, not assumption. Until that finishes, M is an upper bound.</div>",
    ]

    h.append("<h2>Classification</h2><div class=scroll><table>")
    h.append("<tr><th>Status</th><th class=n>Packages</th><th class=n>% of K</th><th></th></tr>")
    colors = {
        "RED_REGRESSION": "var(--red)",
        "GREEN_CURRENT": "var(--green)",
        "NEVER_GREEN": "var(--dim)",
        "FORCED_DOWNGRADE": "var(--amber)",
        "UNKNOWN": "var(--dim)",
    }
    for r in rows(
        "SELECT final_status s, COUNT(*) n FROM packages WHERE in_k=1 GROUP BY s ORDER BY n DESC"
    ):
        pct = r["n"] / k
        c = colors.get(r["s"], "var(--dim)")
        h.append(
            f"<tr><td><code>{r['s']}</code></td><td class=n>{r['n']}</td>"
            f"<td class=n>{pct:.1%}</td>"
            f"<td style='width:38%'><div class=bar><i style='width:{pct*100:.1f}%;background:{c}'></i></div></td></tr>"
        )
    h.append("</table></div>")

    h.append("<h2>Why each red is red</h2><div class=scroll><table>")
    h.append("<tr><th>Basis</th><th class=n>Packages</th></tr>")
    for r in rows(
        "SELECT red_basis b, COUNT(*) n FROM packages WHERE in_k=1 AND "
        "final_status='RED_REGRESSION' GROUP BY b ORDER BY n DESC"
    ):
        h.append(f"<tr><td><code>{r['b']}</code></td><td class=n>{r['n']}</td></tr>")
    h.append("</table></div>")

    repaired_rows = rows(
        "SELECT pkg_key, stars, old_toolchain, tier1_result FROM packages "
        "WHERE tier1_result IS NOT NULL AND json_extract(tier1_result,'$.green')=1 "
        "ORDER BY stars DESC"
    )
    if repaired_rows:
        h.append("<h2>Repaired &amp; kernel-verified</h2><div class=scroll><table>")
        h.append("<tr><th>Package</th><th class=n>Stars</th><th>Was pinned to</th>"
                 "<th class=n>Lines changed</th></tr>")
        for r in repaired_rows:
            info = json.loads(r["tier1_result"])
            h.append(
                f"<tr><td><code>{r['pkg_key']}</code></td><td class=n>{r['stars']}</td>"
                f"<td><code>{(r['old_toolchain'] or '?').replace('leanprover/lean4:','')}</code></td>"
                f"<td class=n>{info.get('substitutions','?')}</td></tr>"
            )
        h.append("</table></div>")

    h.append("<h2>Local reproduction — checking the registry against reality</h2>")
    n_rep = one("SELECT COUNT(*) FROM reproductions WHERE COALESCE(conclusive,1)=1")
    agree = one("SELECT COUNT(*) FROM reproductions WHERE COALESCE(conclusive,1)=1 AND agrees=1")
    h.append(
        f"<p>{agree} of {n_rep} conclusive local rebuilds reproduced the registry's recorded "
        "red, under the same forced toolchain. A build that compiles nothing is recorded as "
        "inconclusive, never as green.</p>"
    )

    h.append(
        "<footer>Every number on this page is a SQL query over "
        f"<code>ledger/colim.sqlite</code>, derived from Reservoir index snapshot "
        f"<code>{get_meta(conn,'index_commit')[:12]}</code>. "
        "Nothing here is estimated or interpolated.</footer></div>"
    )

    OUT.write_text("\n".join(h))
    print(f"wrote {OUT}  (K={k} M={m} N={repaired})")


if __name__ == "__main__":
    main()
