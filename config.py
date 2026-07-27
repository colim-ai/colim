"""Single source of truth for config. Nothing else may hardcode these values."""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = REPO_ROOT / "config.toml"

with CONFIG_PATH.open("rb") as _f:
    CONFIG = tomllib.load(_f)

GITHUB = CONFIG["github"]
CENSUS = CONFIG["census"]
BUILD = CONFIG["build"]
TIER2 = CONFIG["tier2"]
CAMPAIGN = CONFIG["campaign"]

EVAL_TOOLCHAIN: str = CENSUS["eval_toolchain"]
INDEX_COMMIT: str = CENSUS["index_commit"]


def gh_org() -> str:
    """The fork org. Hard-fails rather than ever defaulting to a personal account."""
    org = (GITHUB.get("org") or "").strip()
    if not org:
        raise SystemExit(
            "github.org is unset in config.toml. Set the dedicated fork org "
            "before running anything that writes to GitHub. Refusing to guess."
        )
    return org


def assert_silent_week() -> None:
    """Tripwire for anything that would contact upstream. Call before outreach paths."""
    if CAMPAIGN.get("allow_upstream_prs"):
        raise SystemExit(
            "allow_upstream_prs is true. Week 1 is silent: fork + branch on our own "
            "org only, never a PR/issue/comment upstream. Refusing to proceed."
        )
