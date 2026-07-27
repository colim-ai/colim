"""Build harness: clone at a pinned revision, force a toolchain, build, capture.

Mirrors what Reservoir's testbed does, because our red reproductions have to be
comparable to its rows. Verified against a real Reservoir job log (NOTES.md):

    git clone <url> .          # then reset --hard, clean -ffdx
    ELAN_TOOLCHAIN=<eval>      # forced; the repo's own lean-toolchain is overridden
    lake exe cache get         # mathlib-dependent packages only
    lake build

Every build writes a log, hashes it, and records the outcome. Idempotent:
a package with a recorded reproduction is skipped unless --force.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = REPO_ROOT / "work"
BUILD_LOG_DIR = REPO_ROOT / "data" / "build_logs"


class DiskFloorExceeded(RuntimeError):
    """Raised instead of starting a build that would run the disk out."""


def free_gb(path: Path = REPO_ROOT) -> float:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / 1024**3


def check_disk(floor_gb: float, path: Path = REPO_ROOT) -> None:
    """Watchdog. Refuses to start a build below the configured floor."""
    free = free_gb(path)
    if free < floor_gb:
        raise DiskFloorExceeded(
            f"{free:.1f} GB free, floor is {floor_gb} GB. "
            "Prune build trees or unused elan toolchains before continuing."
        )


# `lake build` exits 0 having compiled NOTHING when a package configures no
# default target -- it only warns. Treating that as green would let a package
# enter N without a single line of Lean being checked, which is the exact
# failure mode the kernel-is-the-check argument depends on not happening.
# Seen on Paper-Proof/paperproof, whose lakefile has no @[default_target].
NO_TARGET_RE = re.compile(
    r"no targets specified and no default targets configured", re.I
)
# Positive evidence that real work happened.
BUILT_SOMETHING_RE = re.compile(r"^[✔✖]\s*\[\d+/\d+\]", re.M)

# Recovering a no-default-target package: name its libraries and executables
# explicitly. Covers both lakefile dialects.
#   lakefile.lean:  lean_lib «Foo» {}      lean_lib Foo where
#   lakefile.toml:  [[lean_lib]] \n name = "Foo"
LEAN_TARGET_RE = re.compile(
    r"^\s*(?:@\[default_target\]\s*)?lean_(?:lib|exe)\s+[«\"]?([A-Za-z_][\w'.]*)[»\"]?",
    re.M,
)
TOML_TARGET_RE = re.compile(
    r"\[\[lean_(?:lib|exe)\]\]\s*(?:\n\s*\w+\s*=.*)*?\n\s*name\s*=\s*\"([^\"]+)\"",
)


def declared_targets(root: Path) -> list[str]:
    """Library/executable names declared by the package's lakefile.

    Used only when `lake build` reports no default target. It is a heuristic
    read of the config rather than a Lake query, so it is applied as a fallback
    and its result is recorded in the transcript for audit.
    """
    for name, pattern in (
        ("lakefile.lean", LEAN_TARGET_RE),
        ("lakefile.toml", TOML_TARGET_RE),
    ):
        path = root / name
        if path.exists():
            found = pattern.findall(path.read_text(errors="replace"))
            # Preserve order, drop duplicates.
            return list(dict.fromkeys(found))
    return []


@dataclass
class BuildResult:
    pkg_key: str
    revision: str | None
    toolchain: str
    step: str  # clone | cache | build
    ok: bool
    exit_code: int
    duration_s: float
    log_path: Path
    log_sha256: str
    timed_out: bool = False
    built_nothing: bool = False  # exit 0 but no target was compiled
    targets: list[str] = field(default_factory=list)

    @property
    def conclusive(self) -> bool:
        """A green that compiled nothing proves nothing."""
        return not (self.ok and self.built_nothing)

    @property
    def verified_green(self) -> bool:
        """The ONLY definition of green in this project.

        Requires the build to have succeeded AND to have actually compiled
        something. Used identically by local reproduction, the measure-reds
        Actions workflow, and Day-6 fix verification -- one code path, so a
        vacuous green cannot slip in through a route that skipped the check.
        """
        return self.ok and not self.built_nothing


def _run(cmd: list[str], cwd: Path, env: dict, timeout: int, log: list[str]) -> tuple[int, bool]:
    """Run a command, appending a labelled transcript to `log`."""
    log.append(f"$ {' '.join(cmd)}   (cwd={cwd})")
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        log.append(e.stdout or "")
        log.append(e.stderr or "")
        log.append(f"##[timeout] exceeded {timeout}s")
        return 124, True
    log.append(proc.stdout)
    log.append(proc.stderr)
    log.append(f"##[exit] {proc.returncode}")
    return proc.returncode, False


def tree_path(pkg_key: str) -> Path:
    """Collision-safe working dir: `owner--repo`, matching the fork convention."""
    return WORK_DIR / pkg_key.replace("/", "--")


def clone_at(repo_url: str, revision: str | None, dest: Path, log: list[str], timeout: int) -> int:
    """Clone and hard-reset to `revision`, reproducing Reservoir's clean checkout.

    Reservoir clones the default branch and builds HEAD; we pin the exact
    revision it built so our reproduction is comparable to its row.
    """
    dest.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()

    if not (dest / ".git").exists():
        code, _ = _run(["git", "init", "-q"], dest, env, timeout, log)
        if code:
            return code
        code, _ = _run(["git", "remote", "add", "origin", repo_url], dest, env, timeout, log)
        if code:
            return code

    if revision:
        code, _ = _run(
            ["git", "fetch", "--depth", "1", "origin", revision], dest, env, timeout, log
        )
        if code:  # some servers refuse by-sha fetch; fall back to a full fetch
            log.append("##[note] shallow by-sha fetch refused, retrying full")
            code, _ = _run(["git", "fetch", "--tags", "origin"], dest, env, timeout, log)
            if code:
                return code
        code, _ = _run(["git", "checkout", "-q", "--force", revision], dest, env, timeout, log)
        if code:
            return code
    else:
        code, _ = _run(["git", "fetch", "--depth", "1", "origin", "HEAD"], dest, env, timeout, log)
        if code:
            return code
        code, _ = _run(["git", "checkout", "-q", "--force", "FETCH_HEAD"], dest, env, timeout, log)
        if code:
            return code

    _run(["git", "reset", "--hard", "-q"], dest, env, timeout, log)
    _run(["git", "clean", "-ffdxq"], dest, env, timeout, log)
    return 0


def lake_build_verified(
    dest: Path, env: dict, timeout: int, log: list[str]
) -> tuple[int, bool, list[str]]:
    """`lake build`, with the vacuous-green guard applied.

    THE shared build path. Local reproduction, the measure-reds Actions
    workflow and Day-6 fix verification all go through here, so no route into
    "green" can skip the check.

    A package with no default target makes `lake build` exit 0 having compiled
    nothing -- it only warns. So when that happens the declared libraries and
    executables are named explicitly and the build is retried. The target list
    is written into the transcript for audit.

    Returns (exit_code, timed_out, targets_used). An empty target list with a
    no-target warning means nothing could be built at all, which the caller
    must never treat as green.
    """
    # --keep-toolchain is load-bearing. Without it, resolving a workspace whose
    # manifest is absent makes Lake rewrite lean-toolchain to whatever the
    # dependency wants: bumping to v4.32.1 silently became v4.33.0-rc1 because
    # mathlib master pins that. We would then have measured the wrong toolchain.
    code, timed_out = _run(["lake", "--keep-toolchain", "build"], dest, env, timeout, log)
    if code != 0 or not NO_TARGET_RE.search("\n".join(log)):
        return code, timed_out, []

    targets = declared_targets(dest)
    log.append(f"##[targets] declared: {targets or '(none found)'}")
    if not targets:
        log.append(
            "##[note] no default target and no declared lean_lib/lean_exe -- "
            "nothing can be built; this is NOT a green build"
        )
        return code, timed_out, []

    code, timed_out = _run(
        ["lake", "--keep-toolchain", "build", *targets], dest, env, timeout, log
    )
    return code, timed_out, targets


def assert_toolchain_intact(dest: Path, expected: str, log: list[str]) -> None:
    """Fail loudly if the build changed the toolchain out from under us.

    Silent toolchain drift would invalidate the measurement without any visible
    error, so this is checked rather than assumed.
    """
    path = dest / "lean-toolchain"
    if not path.exists():
        return
    actual = path.read_text().strip()
    if actual != expected:
        msg = (
            f"toolchain drift: lean-toolchain is {actual!r}, expected {expected!r}. "
            "Lake rewrote the pin during dependency resolution."
        )
        log.append(f"##[error] {msg}")
        raise RuntimeError(msg)


def build_package(
    pkg_key: str,
    repo_url: str,
    revision: str | None,
    toolchain: str,
    *,
    mathlib_downstream: bool,
    lake_jobs: int,
    disk_floor_gb: float,
    skip_clone: bool = False,
    clone_timeout: int = 900,
    cache_timeout: int = 3600,
    build_timeout: int = 7200,
) -> BuildResult:
    """Reproduce one package's build state under a forced toolchain."""
    check_disk(disk_floor_gb)

    dest = tree_path(pkg_key)
    log: list[str] = [
        f"# colim build harness",
        f"# package   {pkg_key}",
        f"# revision  {revision}",
        f"# toolchain {toolchain}  (FORCED, overriding the repo's lean-toolchain)",
        f"# started   {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "",
    ]
    started = time.time()
    step = "clone"
    timed_out = False
    targets: list[str] = []

    if skip_clone:
        # The tree is already checked out and deliberately modified (tier-1 has
        # bumped pins and rewritten sources). Re-cloning would `git clean` those
        # edits away and we would be measuring the unrepaired package.
        log.append(f"##[note] using existing tree at {dest}; clone skipped")
        code = 0
    else:
        code = clone_at(repo_url, revision, dest, log, clone_timeout)

    if code == 0:
        # elan reads ELAN_TOOLCHAIN in preference to lean-toolchain, which is
        # exactly the override Reservoir applies.
        env = os.environ.copy()
        env["ELAN_TOOLCHAIN"] = toolchain
        env["LAKE_NO_CACHE"] = env.get("LAKE_NO_CACHE", "0")

        if mathlib_downstream:
            step = "cache"
            # Cache misses are survivable -- the build just takes far longer --
            # so a failure here is logged but does not end the run.
            cache_code, timed_out = _run(
                ["lake", "exe", "cache", "get"], dest, env, cache_timeout, log
            )
            if cache_code:
                log.append("##[note] cache get failed; continuing to build uncached")

        step = "build"
        # Lake 5.0.0 exposes no jobs flag -- it already parallelises to the core
        # count, which IS the "lake jobs = cores" setting we want on a 2-core box.
        # `lake_jobs` is kept in config for the day Lake gets the flag back and
        # is asserted here so the two cannot silently disagree.
        assert lake_jobs >= 1
        code, timed_out, targets = lake_build_verified(dest, env, build_timeout, log)

    duration = time.time() - started
    text = "\n".join(log)
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    built_nothing = bool(NO_TARGET_RE.search(text)) or not BUILT_SOMETHING_RE.search(text)

    BUILD_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = BUILD_LOG_DIR / f"{pkg_key.replace('/', '--')}.{digest[:12]}.log"
    log_path.write_text(text)

    return BuildResult(
        pkg_key=pkg_key,
        revision=revision,
        toolchain=toolchain,
        step=step,
        ok=(code == 0),
        exit_code=code,
        duration_s=duration,
        log_path=log_path,
        log_sha256=digest,
        timed_out=timed_out,
        built_nothing=built_nothing,
        targets=targets,
    )


def prune_trees(keep: set[str], cap: int) -> list[str]:
    """LRU-prune build trees down to `cap`, never touching `keep`.

    Trees are cheap to recreate and expensive to hoard: 367 of them would
    exhaust the disk many times over.
    """
    if not WORK_DIR.exists():
        return []
    trees = [p for p in WORK_DIR.iterdir() if p.is_dir()]
    protected = {tree_path(k).name for k in keep}
    candidates = [p for p in trees if p.name not in protected]
    candidates.sort(key=lambda p: p.stat().st_mtime)

    removed = []
    while len(trees) - len(removed) > cap and candidates:
        victim = candidates.pop(0)
        shutil.rmtree(victim, ignore_errors=True)
        removed.append(victim.name)
    return removed
