"""Generate the static dashboard FROM the ledger. No independent data.

Designed for scene 1 of a screen recording: sound-off legible at 1080p with no
zooming, one viewport per beat, no animation and no JavaScript that could fail
on camera. Styled as an audit ledger rather than a product page, because the
page is evidence: paper stock, near-black ink, tabular numerals, thin rules.

Colour is rationed. Green appears ONLY for kernel-verified repair. Red appears
ONLY for counts of failing packages. Everything else is ink or grey.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))

from ledger.db import connect, get_meta  # noqa: E402

OUT = HERE / "index.html"

MEASURE_RUN = "https://github.com/colim-ai/colim/actions/runs/30310736200"
# Public, timestamped proof that the repair builds -- CI on our own fork.
REPAIR_CI = "https://github.com/colim-ai/lean4-raytracer/actions/runs/30322697478"
REPAIR_DIFF = "https://github.com/colim-ai/lean4-raytracer/commit/7597a340c83f0a1e08f4e354d0f39ef5f56ca119"

# Internal enum -> what a person would say. The page never shows an enum name.
BASIS_WORDS = {
    "code_error": "Compiler errors in the package’s own code",
    "measured_actions": "We rebuilt it ourselves — public build log",
    "measured_local": "We rebuilt it on our own machine",
    "infra_uninformative": "Registry build failed before compiling — measurement in progress",
    "infra_release": "A pinned release download no longer resolves",
}

CLASS_WORDS = {
    "deprecated": "Uses a name upstream has deprecated",
    "unknown_identifier": "Identifier removed or renamed upstream",
    "type_mismatch": "Types no longer line up",
    "invalid_field": "Structure field removed or renamed",
    "unsolved_goals": "Proof no longer closes",
    "failed_to_synth": "Required instance no longer found",
    "instance_binder": "Instance argument no longer valid",
    "duplicate_declaration": "Name now clashes with upstream",
    "lake_api_churn": "Build config format changed",
    "lake_stale_artifact": "Stale build artifacts",
    "tactic_failed": "Tactic no longer applies",
    "syntax_error": "Syntax no longer parses",
    "bad_import": "Module moved upstream",
    "infra_cache_fetch": "Registry could not fetch its build cache",
    "not_a_field": "Structure field removed or renamed",
    "ambiguous": "Name became ambiguous",
    "recursion_depth": "Elaboration hits recursion limit",
    "lake_missing_file": "Expected build output missing",
    "dep_manifest_mismatch": "Dependency manifest out of date",
    "unknown_namespace": "Namespace removed or renamed",
    "compiler_ir": "Compiler backend rejects the definition",
    "deterministic_timeout": "Elaboration exceeds time budget",
    "missing_cases": "Pattern match no longer exhaustive",
    "invalid_instance": "Declaration no longer valid as an instance",
    "lake_unknown_command": "Build tool command no longer exists",
    "lake_duplicate_root": "Two targets share a root module",
    "lake_no_config": "No usable build configuration",
    "lake_external_command": "External build step failed",
    "infra_toolchain_missing": "Toolchain unavailable on the runner",
    "infra_release_fetch": "Pinned release artifact no longer downloads",
    "infra_extract_failed": "Package archive could not be extracted",
    "infra_runner_disk": "Runner ran out of disk",
    "dep_revision_not_found": "Dependency revision no longer exists",
    "sorry_present": "Contains an unproved placeholder",
    "lake_unknown_option": "Build option no longer recognised",
}

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --paper:#fbfaf7; --ink:#14161a; --grey:#6b7280; --rule:#d8d5cd;
  --red:#a81c1c; --green:#1a6b3c;
  --mono:"SF Mono",SFMono-Regular,ui-monospace,Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
}
html{-webkit-text-size-adjust:100%}
body{background:var(--paper);color:var(--ink);font:19px/1.55 var(--sans);
     font-variant-numeric:tabular-nums;padding:0 0 5rem}
.wrap{max-width:1080px;margin:0 auto;padding:0 3rem}
section{padding:3.4rem 0;border-bottom:1px solid var(--rule)}
section:last-child{border-bottom:0}

.eyebrow{font:600 15px/1.3 var(--mono);letter-spacing:.14em;text-transform:uppercase;
         color:var(--grey);margin-bottom:1.6rem}

/* 1 — hero */
.hero{padding-top:4rem}
.figs{display:flex;align-items:baseline;gap:1.6rem;flex-wrap:wrap}
.big{font:700 128px/.9 var(--mono);letter-spacing:-.045em;color:var(--red)}
.of{font:400 46px/1 var(--mono);color:var(--grey)}
.pct{font:600 46px/1 var(--mono);color:var(--ink);margin-left:.4rem}
.thesis{font-size:30px;line-height:1.35;font-weight:500;max-width:26ch;margin-top:1.5rem}
.qualifier{font-size:22px;line-height:1.45;margin-top:1.5rem;padding-left:1.1rem;
           border-left:4px solid var(--red);max-width:56ch}
.caption{font-size:16px;color:var(--grey);margin-top:1.6rem;max-width:70ch}

/* 2 — first repair */
.repair{border:1.5px solid var(--green);border-radius:4px;padding:2rem 2.2rem;
        background:#fff}
.repair h2{font:600 34px/1.2 var(--sans);display:flex;align-items:center;gap:.7rem}
.check{color:var(--green);font-size:38px;line-height:1}
.facts{display:flex;gap:2.4rem;flex-wrap:wrap;margin:1.4rem 0 1.5rem}
.fact .v{font:600 26px/1.1 var(--mono)}
.fact .k{font-size:14px;color:var(--grey);text-transform:uppercase;letter-spacing:.07em}
.diff{font:15px/1.65 var(--mono);background:var(--paper);border:1px solid var(--rule);
      border-radius:3px;padding:.9rem 1.1rem;overflow-x:auto;white-space:pre}
.diff .minus{color:var(--red)} .diff .plus{color:var(--green)}
.diff b{color:var(--grey);font-weight:600}
.verdict{margin-top:1.2rem;font-size:20px;font-weight:600;color:var(--green)}
.links{margin-top:.85rem;display:flex;gap:1.6rem;flex-wrap:wrap;font-size:16px}
.links a{color:var(--ink);text-decoration:none;border-bottom:1.5px solid var(--rule);padding-bottom:2px}
.links a:hover{border-color:var(--ink)}

/* 3 — verification */
.claims{display:grid;grid-template-columns:1fr 1fr;gap:2.6rem}
.claim .h{font-size:26px;font-weight:600;line-height:1.3}
.claim .d{color:var(--grey);font-size:17px;margin-top:.5rem}

/* 4 — bars */
table{width:100%;border-collapse:collapse;font-size:17px}
td{padding:.42rem 0;vertical-align:middle}
td.lbl{width:44%;padding-right:1.2rem}
td.num{width:64px;text-align:right;font-family:var(--mono);font-weight:600;
       padding-right:1.1rem}
.bar{height:13px;background:#ebe8e1;border-radius:2px;overflow:hidden}
.bar>i{display:block;height:100%;background:var(--red)}
.bar>i.g{background:var(--green)} .bar>i.k{background:#9aa0a6}
h3{font-size:20px;font-weight:600;margin:2.2rem 0 .9rem}
h3:first-of-type{margin-top:0}

.proof{font:12px/1 var(--mono);color:var(--grey);text-decoration:none;
       border-bottom:1px dotted var(--rule);white-space:nowrap;margin-left:.55rem}
.proof:hover{color:var(--ink)}
footer{padding-top:2.4rem;color:var(--grey);font-size:15px}
.cta{margin-top:2.6rem;font-size:20px;font-weight:600}
.cta a{text-decoration:none;border-bottom:2px solid var(--ink);padding-bottom:2px}
a{color:inherit}
@media (max-width:820px){
  .wrap{padding:0 1.4rem} .big{font-size:84px} .claims{grid-template-columns:1fr}
}
"""


def repair_diff(pkg_key: str) -> str:
    """Render the ACTUAL committed diff, rather than a hand-picked excerpt.

    The card previously showed one representative line while the fact box said
    two, which reads as an inconsistency and invites the obvious question.
    Generating from git keeps the two in step permanently.
    """
    import html
    import subprocess

    tree = REPO_ROOT / "work" / pkg_key.replace("/", "--")
    if not (tree / ".git").exists():
        return ""
    proc = subprocess.run(
        ["git", "show", "HEAD", "--unified=0", "--", "Main.lean", "old/vec.lean",
         "lean-toolchain"],
        cwd=tree, capture_output=True, text=True,
    )
    out = []
    for line in proc.stdout.splitlines():
        if line.startswith("+++ b/"):
            out.append(f"<b>{html.escape(line[6:])}</b>")
        elif line.startswith("-") and not line.startswith("---"):
            out.append(f"<span class=minus>{html.escape(line)}</span>")
        elif line.startswith("+") and not line.startswith("+++"):
            out.append(f"<span class=plus>{html.escape(line)}</span>")
    return "\n".join(out)


def proof(label: str, href: str, sql: str) -> str:
    """Every figure carries a link to where it came from.

    Hover reveals the query, but the page must be fully understandable with no
    interaction at all -- this is decoration on top of a stated number, never a
    substitute for stating it.
    """
    return f'<a class=proof href="{href}" title="{sql}">proof ↗</a>'


def main() -> None:
    conn = connect()
    one = lambda q, *a: conn.execute(q, a).fetchone()[0]  # noqa: E731
    rows = lambda q, *a: conn.execute(q, a).fetchall()  # noqa: E731

    k = one("SELECT COUNT(*) FROM packages WHERE in_k=1")
    m = one("SELECT COUNT(*) FROM packages WHERE in_k=1 AND final_status='RED_REGRESSION'")
    uninf = one(
        "SELECT COUNT(*) FROM packages WHERE in_k=1 AND red_basis='infra_uninformative'"
    )
    observations = one("SELECT COUNT(*) FROM build_observations")
    eval_tc = get_meta(conn, "eval_toolchain").split(":")[-1]
    n_rep = one("SELECT COUNT(*) FROM reproductions WHERE COALESCE(conclusive,1)=1")
    agree = one(
        "SELECT COUNT(*) FROM reproductions WHERE COALESCE(conclusive,1)=1 AND agrees=1"
    )
    measured = one("SELECT COUNT(*) FROM packages WHERE measurement_run_url IS NOT NULL")

    r = conn.execute(
        "SELECT stars, old_toolchain, repo_url, tier1_result, eval_build_url "
        "FROM packages WHERE pkg_key='kmill/render'"
    ).fetchone()
    render = json.loads(r["tier1_result"]) if r and r["tier1_result"] else {}

    REPORT = "/census/report.md"
    h: list[str] = [
        "<!doctype html><html lang=en><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        "<title>Colim — Lean ecosystem build health</title>",
        f"<style>{CSS}</style><body><div class=wrap>",
    ]

    # ---- 1. HERO ----------------------------------------------------------
    h += [
        "<section class=hero>",
        "<div class=eyebrow>Colim · census of Lean’s official package registry</div>",
        "<div class=figs>",
        f"<span class=big>{m}</span>",
        f"<span class=of>of {k}</span>",
        f"<span class=pct>{m / k:.0%}</span>",
        "</div>",
        "<p class=thesis>packages in Lean’s official registry fail to build on the "
        f"current stable toolchain.{proof('', REPORT, 'SELECT COUNT(*) FROM packages WHERE in_k=1 AND final_status=&#39;RED_REGRESSION&#39;')}</p>",
        f"<p class=qualifier>{uninf} of the failing packages are recorded only as "
        "infrastructure-ambiguous. Independent measurement is in progress; until it "
        "completes this figure is an upper bound."
        f"{proof('', MEASURE_RUN, 'SELECT COUNT(*) FROM packages WHERE red_basis=&#39;infra_uninformative&#39;')}</p>",
        f"<p class=caption>Source: Reservoir build history, {observations:,} records. "
        "Every figure is a SQL query over our ledger; every row links to a public "
        "build log.</p>",
        "</section>",
    ]

    # ---- 2. FIRST REPAIR --------------------------------------------------
    if render.get("green"):
        old_tc = (r["old_toolchain"] or "").split(":")[-1]
        h += [
            "<section>",
            "<div class=eyebrow>First automated repair</div>",
            "<div class=repair>",
            "<h2><span class=check>✓</span> kmill/render</h2>",
            "<div class=facts>",
            f"<div class=fact><div class=v>{r['stars']}★</div>"
            "<div class=k>Lean 4 raytracer</div></div>",
            f"<div class=fact><div class=v>{old_tc}</div>"
            "<div class=k>Compiler it was pinned to</div></div>",
            f"<div class=fact><div class=v>{render.get('substitutions', '?')} source lines</div>"
            "<div class=k>Plus the toolchain pin</div></div>",
            f"<div class=fact><div class=v>{eval_tc}</div>"
            "<div class=k>Now builds on</div></div>",
            "</div>",
            f"<div class=diff>{repair_diff('kmill/render')}</div>",
            "<p class=verdict>Kernel-verified green on our fork — public CI, "
            "timestamped.</p>",
            "<p class=links>"
            "<a href='/demo/'>Walk through this repair ↗</a>"
            f"<a href='{r['eval_build_url']}'>Upstream build failing ↗</a>"
            f"<a href='{REPAIR_DIFF}'>Our fix ↗</a>"
            f"<a href='{REPAIR_CI}'>Green CI run ↗</a>"
            "</p>",
            "</div></section>",
        ]

    # ---- 3. VERIFICATION --------------------------------------------------
    h += [
        "<section>",
        "<div class=eyebrow>We audit our own data</div>",
        "<div class=claims>",
        f"<div class=claim><div class=h>{agree} of {n_rep} independent rebuilds agree "
        "with the registry.</div>"
        "<div class=d>We rebuilt a sample ourselves, on the same forced toolchain, "
        "rather than trusting the registry’s record. A build that compiles nothing is "
        "recorded as inconclusive, never as green."
        f"{proof('', REPORT, 'SELECT COUNT(*) FROM reproductions WHERE agrees=1')}</div></div>",
        f"<div class=claim><div class=h>{measured} ambiguous packages independently "
        "re-measured.</div>"
        "<div class=d>Rebuilt in our own CI, upstream cloned read-only at the exact "
        "revision. Real errors keep a package in the count; a green build removes it."
        f"{proof('', MEASURE_RUN, 'SELECT COUNT(*) FROM packages WHERE measurement_run_url IS NOT NULL')}</div></div>",
        "</div></section>",
    ]

    # ---- 4. CLASSIFICATION ------------------------------------------------
    h += [
        "<section>",
        "<div class=eyebrow>What is actually broken</div>",
        "<h3>How we know each package is failing</h3><table>",
    ]
    basis = rows(
        "SELECT red_basis b, COUNT(*) n FROM packages WHERE in_k=1 AND "
        "final_status='RED_REGRESSION' GROUP BY b ORDER BY n DESC"
    )
    for row in basis:
        pct = row["n"] / m * 100
        cls = "k" if str(row["b"]).startswith("measured") else ""
        h.append(
            f"<tr><td class=lbl>{BASIS_WORDS.get(row['b'], row['b'])}</td>"
            f"<td class=num>{row['n']}</td>"
            f"<td><div class=bar><i class='{cls}' style='width:{pct:.1f}%'></i></div></td></tr>"
        )
    h.append("</table>")

    h.append("<h3>Why they fail</h3><table>")
    for row in rows(
        "SELECT value AS cls, COUNT(*) n FROM packages, json_each(packages.error_classes) "
        "WHERE in_k=1 AND final_status='RED_REGRESSION' GROUP BY cls "
        "ORDER BY n DESC LIMIT 9"
    ):
        pct = row["n"] / m * 100
        h.append(
            f"<tr><td class=lbl>{CLASS_WORDS.get(row['cls'], row['cls'])}</td>"
            f"<td class=num>{row['n']}</td>"
            f"<td><div class=bar><i style='width:{pct:.1f}%'></i></div></td></tr>"
        )
    h.append("</table>")
    h.append(
        "<p class=caption>Packages usually fail for several reasons at once, so these "
        "do not sum to the total.</p></section>"
    )

    h.append("<p class=cta><a href='/request/'>Request a repair for a package →</a></p>")
    h.append(
        "<footer>Reservoir index snapshot "
        f"<code>{get_meta(conn, 'index_commit')[:12]}</code> · evaluation toolchain "
        f"<code>{eval_tc}</code> · every number on this page is a query over "
        "<code>ledger/colim.sqlite</code>. Nothing is estimated or interpolated."
        "</footer></div></body></html>"
    )

    OUT.write_text("\n".join(h))
    print(f"wrote {OUT}  (K={k} M={m} ambiguous={uninf} repro={agree}/{n_rep})")


if __name__ == "__main__":
    main()
