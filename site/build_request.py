"""Generate /request — submit a package, and see whether we already track it.

There is no backend. The form composes a pre-filled GitHub issue URL and hands
off on submit, so the queue is GitHub's issue list: public, auditable, spam-
filtered, and it notifies the requester when we act. Nothing accepts a write on
the box that holds the ledger.

The lookup is the part that makes this more than a mailbox. The whole census is
baked into the page as a small table (owner/name -> status), so typing a repo
immediately says whether it is already indexed, whether it is failing, and how
we classified it. That is a real answer, delivered instantly, with no request
needed at all -- and for the packages we do not have, it makes clear that the
request is adding something.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))

from ledger.db import connect  # noqa: E402

OUT_DIR = HERE / "request"
ISSUE_NEW = "https://github.com/colim-ai/colim/issues/new"

STATUS_WORDS = {
    "RED_REGRESSION": ("Failing on the current toolchain", "bad"),
    "GREEN_CURRENT": ("Building fine on the current toolchain", "ok"),
    "NEVER_GREEN": ("Never recorded a successful build", "dim"),
    "FORCED_DOWNGRADE": ("Ahead of the evaluation toolchain", "dim"),
    "UNKNOWN": ("Indexed, but we have no usable build record", "dim"),
    "ARCHIVED": ("Archived upstream — outside our scope", "dim"),
    "VANISHED": ("Source no longer resolves on GitHub", "dim"),
}

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--paper:#fbfaf7;--ink:#14161a;--grey:#6b7280;--rule:#d8d5cd;
      --red:#a81c1c;--green:#1a6b3c;
      --mono:"SF Mono",SFMono-Regular,ui-monospace,Menlo,Consolas,monospace;
      --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
body{background:var(--paper);color:var(--ink);font:18px/1.55 var(--sans);
     font-variant-numeric:tabular-nums;padding:2.5rem 1.5rem 5rem}
.wrap{max-width:760px;margin:0 auto}
a{color:inherit}
.top{display:flex;justify-content:space-between;align-items:baseline;
     border-bottom:1px solid var(--rule);padding-bottom:1rem;margin-bottom:2rem;
     flex-wrap:wrap;gap:.6rem}
.top h1{font-size:23px;font-weight:650}
.top .sub{color:var(--grey);font-size:15px}
.lede{font-size:21px;line-height:1.45;margin-bottom:1.8rem;max-width:56ch}
label{display:block;font-size:14px;text-transform:uppercase;letter-spacing:.07em;
      color:var(--grey);margin-bottom:.4rem;font-weight:600}
input,textarea,select{width:100%;font:16px/1.5 var(--mono);padding:.7rem .85rem;
      border:1px solid var(--rule);border-radius:3px;background:#fff;color:var(--ink)}
input:focus,textarea:focus,select:focus{outline:2px solid var(--ink);outline-offset:-1px}
.field{margin-bottom:1.3rem}
.hint{font-size:14px;color:var(--grey);margin-top:.35rem}
button{font:inherit;font-size:17px;padding:.7rem 1.4rem;border:1px solid var(--ink);
       background:var(--ink);color:var(--paper);border-radius:3px;cursor:pointer}
#status{margin:1.1rem 0;padding:.85rem 1.05rem;border-radius:3px;font-size:16px;
        border:1px solid var(--rule);background:#fff;display:none}
#status.show{display:block}
#status .h{font-weight:600}
#status.bad .h{color:var(--red)} #status.ok .h{color:var(--green)}
#status .d{color:var(--grey);font-size:15px;margin-top:.25rem}
.queue{margin-top:3rem;border-top:1px solid var(--rule);padding-top:1.8rem}
.queue h2{font-size:15px;text-transform:uppercase;letter-spacing:.09em;
          color:var(--grey);margin-bottom:1rem;font-weight:600}
.queue ol{list-style:none}
.queue li{padding:.55rem 0;border-bottom:1px solid var(--rule);display:flex;
          justify-content:space-between;gap:1rem;font-size:16px}
.queue li:last-child{border:0}
.queue .when{color:var(--grey);font-size:14px;white-space:nowrap}
.empty{color:var(--grey);font-size:16px}
footer{margin-top:2.5rem;padding-top:1.2rem;border-top:1px solid var(--rule);
       color:var(--grey);font-size:14px}
noscript p{border-left:4px solid var(--red);padding:.6rem 1rem;background:#fff}
@media (max-width:640px){body{padding:1.5rem 1rem 4rem}}
"""

JS = r"""
(function(){
  var d=document, CENSUS=window.__CENSUS__||{}, WORDS=window.__WORDS__||{};
  var repo=d.getElementById('repo'), box=d.getElementById('status'),
      form=d.getElementById('f');

  function norm(v){
    v=(v||'').trim().replace(/^https?:\/\/(www\.)?github\.com\//i,'');
    v=v.replace(/\.git$/,'').replace(/\/+$/,'');
    var p=v.split('/').filter(Boolean);
    return p.length>=2 ? (p[0]+'/'+p[1]).toLowerCase() : '';
  }
  function look(){
    var key=norm(repo.value);
    if(!key){ box.className=''; return; }
    var hit=CENSUS[key];
    box.className='show '+(hit?(WORDS[hit][1]):'');
    if(hit){
      box.innerHTML='<div class=h>Already in our census — '+WORDS[hit][0]+'</div>'+
        '<div class=d>We track this package. Requesting it tells us it matters to '+
        'someone, which moves it up the queue.</div>';
    } else {
      box.innerHTML='<div class=h>Not in our census</div>'+
        '<div class=d>We only index packages listed in Lean’s official registry. '+
        'Request it and we will look.</div>';
    }
  }
  repo.addEventListener('input',look);
  repo.addEventListener('blur',look);

  form.addEventListener('submit',function(e){
    e.preventDefault();
    var key=norm(repo.value)||repo.value.trim();
    if(!key){ repo.focus(); return; }
    var rel=d.getElementById('rel').value,
        ctx=d.getElementById('ctx').value,
        hit=CENSUS[norm(repo.value)];
    var body=
      '### Repository\n'+repo.value.trim()+'\n\n'+
      '### Your relationship to it\n'+rel+'\n\n'+
      '### Anything we should know\n'+(ctx.trim()||'_No response_')+'\n\n'+
      '### Fork permission\n- [X] I understand Colim will fork this repository '+
      'publicly to attempt the repair, and that the fork’s CI results will be public.\n\n'+
      '---\n_Submitted from colim.ai/request'+
      (hit?(' · census status at submission: '+hit):' · not in census')+'_\n';
    var url='https://github.com/colim-ai/colim/issues/new'+
      '?labels=repo-request'+
      '&title='+encodeURIComponent('[request] '+key)+
      '&body='+encodeURIComponent(body);
    window.open(url,'_blank','noopener');
  });
})();
"""


def main() -> None:
    conn = connect()
    census = {
        f"{r['owner']}/{r['name']}".lower(): r["final_status"]
        for r in conn.execute("SELECT owner, name, final_status FROM packages")
    }
    # Also key by the GitHub repo name, which often differs from the registry's
    # normalised package name (leanprover-community/mathlib vs .../mathlib4).
    for r in conn.execute("SELECT repo, final_status FROM packages WHERE repo IS NOT NULL"):
        census.setdefault(r["repo"].lower(), r["final_status"])

    queue = json.loads((HERE / "requests.json").read_text()) if (HERE / "requests.json").exists() else []

    if queue:
        items = "".join(
            f"<li><a href='{q['url']}'>{q['title']}</a>"
            f"<span class=when>{q['created_at'][:10]}</span></li>"
            for q in queue
        )
        queue_html = f"<ol>{items}</ol>"
    else:
        queue_html = (
            "<p class=empty>No open requests yet. Yours would be the first.</p>"
        )

    page = f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Colim — request a package repair</title>
<style>{CSS}</style>
<body><div class=wrap>
<div class=top><h1>Request a repair</h1>
<span class=sub><a href="/">← colim.ai</a></span></div>

<p class=lede>Tell us about a Lean package that no longer builds. Requests are public
issues on our repository, so you can see the whole queue and follow yours.</p>

<noscript><p>This form needs JavaScript to check our census and compose the issue.
You can <a href="{ISSUE_NEW}?labels=repo-request&amp;template=repo-request.yml">file
the request directly on GitHub</a> instead — same queue.</p></noscript>

<form id=f>
  <div class=field>
    <label for=repo>Repository</label>
    <input id=repo name=repo placeholder="https://github.com/owner/name" autocomplete=off>
    <div class=hint>A GitHub URL, or just <code>owner/name</code>.</div>
  </div>
  <div id=status></div>
  <div class=field>
    <label for=rel>Your relationship to it</label>
    <select id=rel>
      <option>I maintain it</option>
      <option>I depend on it</option>
      <option>I just want it to work</option>
      <option>Other</option>
    </select>
  </div>
  <div class=field>
    <label for=ctx>Anything we should know</label>
    <textarea id=ctx rows=4 placeholder="Optional. A branch to target, parts that are expected to be broken, a toolchain you need."></textarea>
  </div>
  <button type=submit>Open the request on GitHub →</button>
  <div class=hint>Opens a pre-filled issue. You review it before posting, and nothing
  is sent until you do.</div>
</form>

<div class=queue>
  <h2>Open requests</h2>
  {queue_html}
</div>

<footer>We work only on our own forks and never open a pull request against your
repository unless you ask. A repair counts only if it builds green and leaves every
declaration name and type unchanged.</footer>
</div>
<script>
window.__CENSUS__={json.dumps(census, separators=(",", ":"))};
window.__WORDS__={json.dumps(STATUS_WORDS, separators=(",", ":"))};
</script>
<script>{JS}</script>
</body></html>"""

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "index.html").write_text(page)
    print(
        f"wrote {OUT_DIR / 'index.html'}  ({len(page):,} bytes, "
        f"{len(census)} census keys, {len(queue)} open request(s))"
    )


if __name__ == "__main__":
    main()
