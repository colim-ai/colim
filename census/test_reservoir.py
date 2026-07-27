"""Regression tests for the census parser.

These exist because the mathlib-dependency bug was SILENT: it produced 262
downstream packages instead of 495 and looked entirely plausible. Nothing
crashed, no number was obviously wrong. Any future edit to dependency matching
must keep these green.

Run: python3 -m pytest census/test_reservoir.py -q
     (or: python3 census/test_reservoir.py  -- no pytest needed)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reservoir import MATHLIB_REPOS, load_all, normalize_repo, version_key  # noqa: E402

# The two failure modes that caused the bug, as literal fixtures:
#   1. mathlib's dependency URL carries a `.git` suffix
#   2. mathlib is recorded with scope=null, unlike aesop/batteries/plausible
MATHLIB_DEP_URL = "https://github.com/leanprover-community/mathlib4.git"

# Real packages that are unambiguously mathlib-downstream. If any of these
# reports False, dependency matching has regressed.
KNOWN_DOWNSTREAM = [
    "ImperialCollegeLondon/flt",
    "teorth/analysis",
    "AlexKontorovich/primenumbertheoremand",
]


def test_normalize_strips_git_suffix():
    assert normalize_repo(MATHLIB_DEP_URL) == "leanprover-community/mathlib4"
    assert normalize_repo(MATHLIB_DEP_URL) in MATHLIB_REPOS


def test_normalize_url_variants():
    for url in (
        "https://github.com/leanprover-community/mathlib4",
        "https://github.com/leanprover-community/mathlib4/",
        "https://github.com/LeanProver-Community/MathLib4.git",
        "git@github.com:leanprover-community/mathlib4.git",
    ):
        assert normalize_repo(url) == "leanprover-community/mathlib4", url


def test_normalize_rejects_non_github():
    assert normalize_repo(None) is None
    assert normalize_repo("") is None
    assert normalize_repo("https://gitlab.com/a/b") is None


def test_known_mathlib_downstream_packages():
    """The .git-suffix + null-scope fixture check, against real index data."""
    pkgs = {p.key.lower(): p for p in load_all()}
    for key in KNOWN_DOWNSTREAM:
        pkg = pkgs.get(key.lower())
        assert pkg is not None, f"{key} missing from the index snapshot"
        assert pkg.depends_on_mathlib() is True, (
            f"{key} must be detected as mathlib-downstream -- this is the "
            "regression that silently halved K_m"
        )


def test_mathlib_is_not_downstream_of_itself():
    pkgs = {p.key: p for p in load_all()}
    assert pkgs["leanprover-community/mathlib"].depends_on_mathlib() is False


def test_dependency_scope_is_untrustworthy_for_mathlib():
    """Pins the upstream quirk that broke the first implementation.

    If Reservoir ever starts populating scope for mathlib this test fails,
    which is the signal to revisit the matching logic -- not to delete it.
    """
    pkgs = {p.key.lower(): p for p in load_all()}
    deps = pkgs["imperialcollegelondon/flt"].latest_version.dependencies
    mathlib_dep = next(d for d in deps if normalize_repo(d["url"]) in MATHLIB_REPOS)
    assert mathlib_dep["scope"] is None
    assert mathlib_dep["url"].endswith(".git")


def test_version_key_orders_toolchains():
    assert version_key("leanprover/lean4:v4.33.0-rc1") > version_key("leanprover/lean4:v4.32.1")
    assert version_key("leanprover/lean4:v4.32.1") > version_key("leanprover/lean4:v4.32.0")
    assert version_key("leanprover/lean4:v4.9.0") < version_key("leanprover/lean4:v4.10.0")


def test_forced_downgrade_packages_are_ahead_of_eval():
    """mathlib and batteries must never be counted as red regressions."""
    from config import EVAL_TOOLCHAIN

    pkgs = {p.key: p for p in load_all()}
    for key in ("leanprover-community/mathlib", "leanprover-community/batteries"):
        pinned = pkgs[key].pinned_toolchain
        assert version_key(pinned) > version_key(EVAL_TOOLCHAIN), (
            f"{key} pins {pinned}, expected newer than eval toolchain"
        )


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                failed += 1
                print(f"  FAIL  {name}: {e}")
    print(f"\n{'FAILED' if failed else 'all tests passed'}")
    sys.exit(1 if failed else 0)
