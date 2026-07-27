"""Parse a pinned Reservoir index snapshot.

Read-only over data/raw/reservoir-index. See NOTES.md for the empirically
verified shape of every file this touches. Nothing here hits the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO_ROOT / "data" / "raw" / "reservoir-index"

# Packages that ship as part of the Lean release train. In K (they classify
# like anything else), excluded from the campaign. See CLAUDE.md census scope.
RELEASE_TRAIN = {
    "leanprover-community/mathlib",
    "leanprover-community/batteries",
    "leanprover-community/aesop",
    "leanprover-community/proofwidgets",
    "leanprover-community/plausible",
    "leanprover-community/importgraph",
    "leanprover-community/leansearchclient",
    "leanprover-community/qq",
}

# Matched against normalised "<owner>/<repo>", lowercased. Reservoir records
# mathlib with scope=None and a .git-suffixed URL, so neither the scope field
# nor a raw URL comparison is trustworthy -- normalise, then match.
MATHLIB_REPOS = {
    "leanprover-community/mathlib4",
    "leanprover-community/mathlib",
}


def normalize_repo(url: str | None) -> str | None:
    """`https://github.com/Owner/Repo.git` -> `owner/repo`; None if not parseable."""
    if not url:
        return None
    s = url.strip().rstrip("/")
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if s.lower().startswith(prefix.lower()):
            s = s[len(prefix) :]
            break
    else:
        return None
    if s.lower().endswith(".git"):
        s = s[:-4]
    parts = s.split("/")
    return f"{parts[0]}/{parts[1]}".lower() if len(parts) >= 2 else None


@dataclass
class Build:
    toolchain: str
    revision: str | None
    built: bool
    run_at: str
    url: str | None


@dataclass
class Version:
    revision: str | None
    date: str | None
    tag: str | None
    toolchain: str | None
    dependencies: list[dict] = field(default_factory=list)


@dataclass
class Package:
    owner: str
    name: str
    full_name: str
    stars: int
    license: str | None
    created_at: str | None
    updated_at: str | None
    source_full_name: str | None
    repo_url: str | None
    default_branch: str | None
    builds: list[Build]
    versions: list[Version]

    @property
    def key(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def latest_version(self) -> Version | None:
        return self.versions[0] if self.versions else None

    @property
    def pinned_toolchain(self) -> str | None:
        """The repo's own lean-toolchain at its most recent indexed revision."""
        v = self.latest_version
        return v.toolchain if v else None

    def outcome_on(self, toolchain: str) -> bool | None:
        """Most recent recorded outcome on `toolchain`, or None if unrecorded.

        Reservoir retries; entries are newest-first. Taking the most recent
        makes "recorded outcome" single-valued, which the coverage rule needs.
        """
        for b in self.builds:  # newest first, verified in NOTES.md
            if b.toolchain == toolchain:
                return b.built
        return None

    def build_on(self, toolchain: str) -> Build | None:
        for b in self.builds:
            if b.toolchain == toolchain:
                return b
        return None

    def ever_green(self) -> bool:
        return any(b.built for b in self.builds)

    def ever_green_before(self, run_at: str) -> bool:
        """Any successful build strictly earlier than `run_at`.

        This is the "successful build on some earlier toolchain" half of
        RED_REGRESSION. Ordering is by wall-clock run time rather than by
        parsed version, because Reservoir interleaves rc and stable runs.
        """
        return any(b.built and b.run_at < run_at for b in self.builds)

    def depends_on_mathlib(self) -> bool | None:
        """None when we have no version record to judge from.

        Reservoir's dependency list is the flattened closure (it carries a
        `transitive` flag), so a direct scan catches indirectly-downstream
        packages too -- no graph walk needed.
        """
        v = self.latest_version
        if v is None:
            return None
        if self.key in MATHLIB_REPOS or normalize_repo(self.repo_url) in MATHLIB_REPOS:
            return False  # mathlib is not downstream of itself
        for d in v.dependencies:
            if normalize_repo(d.get("url")) in MATHLIB_REPOS:
                return True
        return False

    @property
    def is_release_train(self) -> bool:
        return self.key in RELEASE_TRAIN


def _read(path: Path) -> list:
    """Builds/versions files are {schemaVersion, data: [...]}.

    Older entries in the snapshot are a bare list instead -- the schema gained
    its envelope partway through the index's life. Accept both.
    """
    with path.open() as f:
        doc = json.load(f)
    return doc if isinstance(doc, list) else doc["data"]


def load_package(pkg_dir: Path) -> Package:
    meta = json.loads((pkg_dir / "metadata.json").read_text())
    src = (meta.get("sources") or [{}])[0]

    builds = [
        Build(
            toolchain=b["toolchain"],
            revision=b.get("revision"),
            # Legacy entries (2 rows, katzenpost/crypt_walker) spell these
            # `outcome`/`builtAt` instead of `built`/`runAt`. See NOTES.md.
            built=bool(b["built"]) if "built" in b else b.get("outcome") == "success",
            run_at=b.get("runAt") or b["builtAt"],
            url=b.get("url"),
        )
        for b in _read(pkg_dir / "builds.json")
    ]
    builds.sort(key=lambda b: b.run_at, reverse=True)

    # 3 of 790 packages have no versions.json at all: Reservoir never got a
    # revision indexed for them. They keep an empty version list, which makes
    # pinned_toolchain / depends_on_mathlib None -> UNKNOWN, never RED.
    versions_path = pkg_dir / "versions.json"
    versions = [
        Version(
            revision=v.get("revision"),
            date=v.get("date"),
            tag=v.get("tag"),
            toolchain=v.get("toolchain"),
            dependencies=v.get("dependencies") or [],
        )
        for v in (_read(versions_path) if versions_path.exists() else [])
    ]
    versions.sort(key=lambda v: v.date or "", reverse=True)

    return Package(
        owner=meta["owner"],
        name=meta["name"],
        full_name=meta["fullName"],
        stars=meta.get("stars") or 0,
        license=meta.get("license"),
        created_at=meta.get("createdAt"),
        updated_at=meta.get("updatedAt"),
        source_full_name=src.get("fullName"),
        repo_url=src.get("repoUrl"),
        default_branch=src.get("defaultBranch"),
        builds=builds,
        versions=versions,
    )


def load_all(snapshot: Path = SNAPSHOT) -> list[Package]:
    """Every indexed package, one per directory holding a metadata.json.

    Alias entries are plain files, not directories, so globbing for
    metadata.json skips them and no dedup pass is needed.
    """
    pkgs = [load_package(p.parent) for p in sorted(snapshot.glob("*/*/metadata.json"))]
    if not pkgs:
        raise SystemExit(f"no packages under {snapshot} — is the snapshot cloned?")
    return pkgs


def snapshot_commit(snapshot: Path = SNAPSHOT) -> str:
    head = (snapshot / ".git" / "HEAD").read_text().strip()
    if head.startswith("ref: "):
        return (snapshot / ".git" / head[5:]).read_text().strip()
    return head


def is_stable(toolchain: str) -> bool:
    """Stable == a plain vX.Y.Z release, no -rc / -nightly suffix."""
    return "-rc" not in toolchain and "nightly" not in toolchain


def version_key(toolchain: str) -> tuple:
    """Sortable key for `leanprover/lean4:v4.32.1`; unparseable sorts first."""
    tag = toolchain.split(":")[-1].lstrip("v")
    base = tag.split("-")[0]
    try:
        return tuple(int(x) for x in base.split("."))
    except ValueError:
        return (0,)
