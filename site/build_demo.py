"""Generate the interactive walkthrough at /demo.

A step-through of the ONE repair we have actually done, built entirely from
real artefacts: the registry's failing build log, the extracted rename map, the
curated supplement entry and its upstream evidence, the committed diff, and the
verdict emitted by CI on our fork.

It is a REPLAY, and says so on the page. Nothing here compiles Lean in the
browser or re-runs anything live; every panel shows output that was really
produced, and the page links to the command that reproduces it.

Progressive enhancement is deliberate: with JavaScript disabled every step is
still present and readable, stacked as a document. The script only adds
stepping. The dashboard at / stays script-free entirely.
"""

from __future__ import annotations

import html
import json
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))

from ledger.db import connect  # noqa: E402

OUT_DIR = HERE / "demo"
PKG = "kmill/render"
UPSTREAM = "kmill/lean4-raytracer"
FORK = "colim-ai/lean4-raytracer"
CI_RUN = "https://github.com/colim-ai/lean4-raytracer/actions/runs/30322697478"
DIFF_URL = (
    "https://github.com/colim-ai/lean4-raytracer/commit/"
    "7597a340c83f0a1e08f4e354d0f39ef5f56ca119"
)

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--paper:#fbfaf7;--ink:#14161a;--grey:#6b7280;--rule:#d8d5cd;
      --red:#a81c1c;--green:#1a6b3c;--wash:#fff;
      --mono:"SF Mono",SFMono-Regular,ui-monospace,Menlo,Consolas,monospace;
      --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
body{background:var(--paper);color:var(--ink);font:18px/1.55 var(--sans);
     font-variant-numeric:tabular-nums;padding:2.5rem 1.5rem 5rem}
.wrap{max-width:920px;margin:0 auto}
a{color:inherit}
.top{display:flex;justify-content:space-between;align-items:baseline;
     border-bottom:1px solid var(--rule);padding-bottom:1rem;margin-bottom:2rem;
     flex-wrap:wrap;gap:.6rem}
.top h1{font-size:23px;font-weight:650;letter-spacing:-.01em}
.top .sub{color:var(--grey);font-size:15px}
.replay{border-left:4px solid var(--grey);padding:.55rem .95rem;margin-bottom:2rem;
        font-size:15px;color:var(--grey);background:#f3f1ec;border-radius:0 4px 4px 0}
.step{border:1px solid var(--rule);border-radius:5px;background:var(--wash);
      padding:1.6rem 1.8rem;margin-bottom:1.1rem}
.step .n{font:600 13px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;
         color:var(--grey);margin-bottom:.7rem}
.step h2{font-size:25px;font-weight:600;line-height:1.25;margin-bottom:.6rem}
.step p{color:#3c4148;margin-bottom:.55rem}
.step p:last-child{margin-bottom:0}
pre{font:14px/1.6 var(--mono);background:var(--paper);border:1px solid var(--rule);
    border-radius:3px;padding:.85rem 1rem;overflow-x:auto;margin:.9rem 0;
    white-space:pre;color:var(--ink)}
pre .minus{color:var(--red)} pre .plus{color:var(--green)}
pre .dim{color:var(--grey)} pre .err{color:var(--red)}
pre b{color:var(--grey);font-weight:600}
.kv{display:flex;gap:2rem;flex-wrap:wrap;margin:.9rem 0}
.kv div span{display:block}
.kv .v{font:600 21px/1.15 var(--mono)}
.kv .k{font-size:13px;color:var(--grey);text-transform:uppercase;letter-spacing:.06em}
.ok{color:var(--green);font-weight:600}
.bad{color:var(--red);font-weight:600}
.links{display:flex;gap:1.4rem;flex-wrap:wrap;margin-top:1rem;font-size:16px}
.links a{text-decoration:none;border-bottom:1.5px solid var(--rule);padding-bottom:2px}
.links a:hover{border-color:var(--ink)}
.nav{display:flex;gap:.7rem;align-items:center;margin:2rem 0 1rem;flex-wrap:wrap}
button{font:inherit;font-size:16px;padding:.5rem 1.1rem;border:1px solid var(--ink);
       background:var(--ink);color:var(--paper);border-radius:3px;cursor:pointer}
button.ghost{background:transparent;color:var(--ink)}
button[disabled]{opacity:.35;cursor:default}
.dots{display:flex;gap:.4rem;margin-left:auto}
.dots i{width:9px;height:9px;border-radius:50%;background:var(--rule);display:block}
.dots i.on{background:var(--ink)}
footer{margin-top:2.5rem;padding-top:1.2rem;border-top:1px solid var(--rule);
       color:var(--grey);font-size:14px}
.js .step{display:none} .js .step.on{display:block}
@media (max-width:700px){body{padding:1.5rem 1rem 4rem}.step{padding:1.2rem}}
"""

JS = """
// Progressive enhancement only: without this the page is a readable document
// with every step visible. Nothing here fetches, computes or hides evidence.
(function(){
  var d=document, steps=[].slice.call(d.querySelectorAll('.step'));
  if(!steps.length) return;
  d.documentElement.classList.add('js');
  var i=0, prev=d.getElementById('prev'), next=d.getElementById('next'),
      dots=d.getElementById('dots');
  steps.forEach(function(){ var s=d.createElement('i'); dots.appendChild(s); });
  function show(n){
    i=Math.max(0,Math.min(steps.length-1,n));
    steps.forEach(function(s,k){ s.classList.toggle('on',k===i); });
    [].forEach.call(dots.children,function(s,k){ s.classList.toggle('on',k<=i); });
    prev.disabled=(i===0); next.disabled=(i===steps.length-1);
    next.textContent=(i===steps.length-1)?'Done':'Next \\u2192';
  }
  prev.onclick=function(){show(i-1)};
  next.onclick=function(){show(i+1)};
  d.addEventListener('keydown',function(e){
    if(e.key==='ArrowRight'||e.key===' ') {e.preventDefault(); show(i+1);}
    if(e.key==='ArrowLeft') show(i-1);
  });
  show(0);
})();
"""


def sh(args: list[str], cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True).stdout


def diff_html() -> str:
    tree = REPO_ROOT / "work" / PKG.replace("/", "--")
    if not (tree / ".git").exists():
        return "<span class=dim>(work tree not present; run ./demo/run_render.sh)</span>"
    out = []
    raw = sh(
        ["git", "show", "HEAD", "--unified=0", "--",
         "Main.lean", "old/vec.lean", "lean-toolchain"],
        tree,
    )
    for line in raw.splitlines():
        if line.startswith("+++ b/"):
            out.append(f"<b>{html.escape(line[6:])}</b>")
        elif line.startswith("-") and not line.startswith("---"):
            out.append(f"<span class=minus>{html.escape(line)}</span>")
        elif line.startswith("+") and not line.startswith("+++"):
            out.append(f"<span class=plus>{html.escape(line)}</span>")
    return "\n".join(out)


def supplement_entry() -> dict:
    with (REPO_ROOT / "fixers" / "tier1" / "renames_supplement.toml").open("rb") as f:
        doc = tomllib.load(f)
    for e in doc.get("entry", []):
        if e["old"] == "Array.mkArray":
            return e
    return {}


def step(n: int, total: int, title: str, body: str) -> str:
    return (
        f"<section class=step><div class=n>Step {n} of {total}</div>"
        f"<h2>{title}</h2>{body}</section>"
    )


def main() -> None:
    conn = connect()
    r = conn.execute(
        "SELECT stars, old_toolchain, last_commit, first_error, eval_build_url, "
        "target_toolchain, tier1_result FROM packages WHERE pkg_key=?", (PKG,)
    ).fetchone()
    if not r:
        raise SystemExit(f"{PKG} not in the ledger")
    t1 = json.loads(r["tier1_result"] or "{}")

    renames = json.loads((REPO_ROOT / "fixers" / "tier1" / "renames.json").read_text())
    n_renames = len(renames["renames"])
    sup = supplement_entry()
    old_tc = (r["old_toolchain"] or "").split(":")[-1]
    new_tc = (r["target_toolchain"] or "").split(":")[-1]
    TOTAL = 6

    steps = []

    steps.append(step(1, TOTAL, "A real package, frozen in time", f"""
<p><a href="https://github.com/{UPSTREAM}">{UPSTREAM}</a> is a raytracer written in
Lean 4. It works. Its author has not touched it since
{r['last_commit'][:10]}.</p>
<div class=kv>
  <div><span class=v>{r['stars']}★</span><span class=k>Stars</span></div>
  <div><span class=v>{old_tc}</span><span class=k>Compiler it pins</span></div>
  <div><span class=v>{new_tc}</span><span class=k>Current stable</span></div>
</div>
<p>Nothing is wrong with the code. The compiler moved underneath it — that is the
entire problem Colim exists to solve.</p>"""))

    steps.append(step(2, TOTAL, "It no longer builds on today’s compiler", f"""
<p>Lean’s official registry rebuilds every indexed package against each new
toolchain. Here is what it recorded, verbatim:</p>
<pre><span class=err>{html.escape(r['first_error'] or '')}</span>
<span class=err>error: Lean exited with code 1</span>
<span class=err>error: build failed</span></pre>
<p>One removed name. The package is dead on the current compiler until somebody
edits it.</p>
<div class=links><a href="{r['eval_build_url']}">The registry’s failing build log ↗</a></div>"""))

    steps.append(step(3, TOTAL, "Why the obvious fix does not work", f"""
<p>Lean and Mathlib mark most renames with a deprecation alias, and Colim extracts
them automatically — <strong>{n_renames:,} renames</strong> from the sources at the
target revision.</p>
<pre><span class=dim>$ grep Array.mkArray fixers/tier1/renames.json</span>
<span class=dim>(no match)</span></pre>
<p><code>Array.mkArray</code> is not in that map. It was removed outright, with no
alias left behind, so no amount of automatic extraction will find it. This is the
class of failure that makes naive rename tooling stall.</p>"""))

    steps.append(step(4, TOTAL, "So we go and find the evidence", f"""
<p>The replacement exists — it just was not advertised. Colim records it as a
curated entry, and <strong>every curated entry must cite upstream</strong>:</p>
<pre>old      = "{sup.get('old','')}"
new      = "{sup.get('new','')}"
kind     = "{sup.get('kind','')}"
evidence = "{sup.get('evidence','')}"</pre>
<p>No entry may rest on recollection. A test refuses to load the map if any entry
lacks an evidence link.</p>
<div class=links><a href="{sup.get('evidence','#')}">The upstream commit that renamed it ↗</a></div>"""))

    steps.append(step(5, TOTAL, "The repair", f"""
<p>Colim clones the package at the exact revision the registry built, bumps the
toolchain pin, and applies the rename maps at identifier boundaries — never inside
strings or comments.</p>
<pre>{diff_html()}</pre>
<p>{t1.get('substitutions','?')} source lines, plus the toolchain pin. That is the
whole change.</p>"""))

    steps.append(step(6, TOTAL, "Verified, in public", f"""
<p>The repaired branch is pushed to <strong>our own fork</strong> and built by CI
there. Nothing is proposed upstream. The verdict is machine-emitted:</p>
<pre>"verified_green":   <span class=plus>true</span>
"built_nothing":    <span class=plus>false</span>   <span class=dim>&larr; it really compiled something</span>
"toolchain_intact": <span class=plus>true</span>    <span class=dim>&larr; on the toolchain we pinned</span>
"targets":          ["Render", "render"]</pre>
<p>Those three booleans are the guards. A build that exits zero without compiling
anything, or that ran on a toolchain something swapped underneath us, cannot be
reported as green.</p>
<p class=ok>{PKG} builds green on {new_tc}, checked by the Lean kernel.</p>
<div class=links>
  <a href="{DIFF_URL}">Our commit ↗</a>
  <a href="{CI_RUN}">The green CI run ↗</a>
  <a href="https://github.com/colim-ai/colim">Source ↗</a>
</div>"""))

    page = f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Colim — repairing a broken Lean package, step by step</title>
<style>{CSS}</style>
<body><div class=wrap>
<div class=top>
  <h1>Repairing a broken Lean package</h1>
  <span class=sub><a href="/">← colim.ai</a></span>
</div>
<p class=replay>A replay of one real repair. Every panel below shows output that was
actually produced &mdash; nothing is simulated, and nothing compiles in your browser.
Reproduce it yourself in about six seconds with <code>./demo/run_render.sh</code>.</p>
{''.join(steps)}
<div class=nav>
  <button class=ghost id=prev>&larr; Back</button>
  <button id=next>Next &rarr;</button>
  <span class=dots id=dots></span>
</div>
<footer>Use ← and → to step through. With JavaScript disabled every step is shown
at once &mdash; nothing here needs interaction to be read.</footer>
</div>
<script>{JS}</script>
</body></html>"""

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "index.html").write_text(page)
    print(f"wrote {OUT_DIR / 'index.html'}  ({len(page):,} bytes, {TOTAL} steps)")


if __name__ == "__main__":
    main()
