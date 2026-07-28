"""Publish a repair to our own fork so the green build is publicly checkable.

Week 1 is silent. This forks upstream into OUR org, pushes a branch to OUR
fork, and runs CI on OUR fork. It opens no pull request, files no issue, and
sends no message to any maintainer -- `config.assert_silent_week()` is called
before anything touches the network.

The point is evidence a stranger can check without trusting us: upstream's red
build log, our branch diff, and a timestamped green CI run, in one chain.

  python3 campaign/publish_repair.py kmill/render
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
for p in (REPO_ROOT, REPO_ROOT / "harness"):
    sys.path.insert(0, str(p))

from build import tree_path  # noqa: E402
from config import CONFIG, assert_silent_week, gh_org  # noqa: E402
from ledger.db import connect, now  # noqa: E402

TARGET = CONFIG["campaign_target"]
BRANCH = CONFIG["github"]["branch_template"].format(
    toolchain=TARGET["toolchain"].split(":")[-1]
)
WORKFLOW_SRC = HERE / "workflow_template.yml"


def gh(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, **kw)


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def ensure_fork(upstream: str, org: str) -> str:
    """Fork upstream into our org, or return the existing fork."""
    name = upstream.split("/")[-1]
    target = f"{org}/{name}"

    if gh(["api", f"repos/{target}"]).returncode == 0:
        print(f"    fork already exists: {target}")
        return target

    print(f"    forking {upstream} -> {org}")
    proc = gh(["repo", "fork", upstream, "--org", org, "--clone=false"])
    if proc.returncode:
        raise SystemExit(f"fork failed: {proc.stderr.strip()}")

    # Forking is asynchronous; wait for the repo to actually exist.
    for _ in range(30):
        if gh(["api", f"repos/{target}"]).returncode == 0:
            return target
        time.sleep(2)
    raise SystemExit(f"fork {target} did not appear in time")


def enable_actions(repo: str) -> None:
    """Actions are disabled by default on forks. Fail loudly if we cannot."""
    proc = gh(["api", "-X", "PUT", f"repos/{repo}/actions/permissions",
               "-F", "enabled=true", "-f", "allowed_actions=all"])
    if proc.returncode:
        raise SystemExit(
            f"could not enable Actions on {repo}: {proc.stderr.strip()}\n"
            "Without Actions there is no public green check, which is the "
            "entire point -- refusing to continue silently."
        )
    print(f"    Actions enabled on {repo}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg_key")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Tripwire: refuses to run if anyone has flipped on upstream PRs.
    assert_silent_week()

    conn = connect()
    row = conn.execute(
        "SELECT repo_url, repo, tier1_result FROM packages WHERE pkg_key=?",
        (args.pkg_key,),
    ).fetchone()
    if not row:
        raise SystemExit(f"{args.pkg_key} is not in the ledger")
    if not row["tier1_result"] or not json.loads(row["tier1_result"]).get("green"):
        raise SystemExit(
            f"{args.pkg_key} has no verified-green tier-1 result. "
            "Only a repair that already built green here may be published."
        )

    upstream = row["repo"] or row["repo_url"].rsplit("/", 2)[-2] + "/" + row["repo_url"].rsplit("/", 1)[-1]
    tree = tree_path(args.pkg_key)
    if not (tree / ".git").exists():
        raise SystemExit(f"no repaired work tree at {tree}; run the tier-1 pipeline first")

    org = gh_org()
    print(f"publishing {args.pkg_key}")
    print(f"  upstream : {upstream}")
    print(f"  org      : {org}")
    print(f"  branch   : {BRANCH}")
    if args.dry_run:
        print("  (dry run, nothing done)")
        return

    fork = ensure_fork(upstream, org)
    enable_actions(fork)

    # Inject the verification workflow into the repaired tree.
    wf_dir = tree / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "colim-verify.yml").write_text(WORKFLOW_SRC.read_text())

    git(["config", "user.name", "Colim"], tree)
    git(["config", "user.email", "noreply@colim.ai"], tree)
    git(["checkout", "-B", BRANCH], tree)
    git(["add", "-A"], tree)

    subs = json.loads(row["tier1_result"]).get("substitutions", "?")
    message = (
        f"Repair for {TARGET['toolchain']}\n\n"
        f"Automated repair by Colim. {subs} source substitution(s) plus a "
        f"toolchain bump to {TARGET['toolchain']}.\n\n"
        "Only proof bodies, tactic scripts, imports and build configuration are "
        "eligible to change; declaration names and their types are unchanged.\n\n"
        "This branch lives on a fork and is not proposed upstream."
    )
    commit = git(["commit", "-m", message], tree)
    if commit.returncode and "nothing to commit" not in commit.stdout:
        raise SystemExit(f"commit failed: {commit.stdout}{commit.stderr}")

    # SSH, not HTTPS: gh is configured for SSH git operations here, and an
    # HTTPS remote prompts for a username that a non-interactive run cannot
    # supply. Fall back to gh's credential helper if SSH is unavailable.
    git(["remote", "remove", "colim"], tree)
    git(["remote", "add", "colim", f"git@github.com:{fork}.git"], tree)
    push = git(["push", "-f", "colim", BRANCH], tree)
    if push.returncode:
        print("    ssh push failed, retrying over https with gh credentials")
        git(["remote", "set-url", "colim", f"https://github.com/{fork}.git"], tree)
        subprocess.run(["gh", "auth", "setup-git"], capture_output=True, text=True)
        push = git(["push", "-f", "colim", BRANCH], tree)
    if push.returncode:
        raise SystemExit(f"push failed: {push.stderr.strip()}")

    sha = git(["rev-parse", "HEAD"], tree).stdout.strip()
    branch_url = f"https://github.com/{fork}/tree/{BRANCH}"
    compare_url = f"https://github.com/{fork}/commit/{sha}"
    print(f"    pushed {sha[:9]} to {fork}:{BRANCH}")
    print(f"    branch  {branch_url}")
    print(f"    diff    {compare_url}")

    conn.execute(
        "UPDATE packages SET branch_url=?, updated_at=? WHERE pkg_key=?",
        (compare_url, now(), args.pkg_key),
    )
    conn.commit()
    print("\nCI is starting on the fork. Poll with:")
    print(f"  gh run list --repo {fork} --workflow colim-verify.yml")


if __name__ == "__main__":
    main()
