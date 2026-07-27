"""Tests for the tier-1 bump script.

The bug these exist to prevent was silent and expensive: a Mathlib `require`
with no `@ "rev"` clause was skipped, the package kept tracking mathlib master,
and Lake then rewrote lean-toolchain to master's v4.33.0-rc1. The build ran
green-or-red on the WRONG toolchain with no error anywhere.

Run: python3 fixers/tier1/test_bump.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bump import bump  # noqa: E402

TC = "leanprover/lean4:v4.32.1"
TAG = "v4.32.1"


def scratch(files: dict[str, str]) -> Path:
    d = Path(tempfile.mkdtemp(prefix="colim-bump-"))
    for name, text in files.items():
        (d / name).write_text(text)
    return d


def test_pins_unpinned_multiline_require():
    """The exact shape that broke: no @ rev, URL on its own line."""
    d = scratch(
        {
            "lean-toolchain": "leanprover/lean4:v4.5.0-rc1\n",
            "lakefile.lean": (
                "import Lake\n"
                "require mathlib from git\n"
                '  "https://github.com/leanprover-community/mathlib4.git"\n'
            ),
        }
    )
    res = bump(d, toolchain=TC, mathlib_tag=TAG)
    text = (d / "lakefile.lean").read_text()
    assert f'@ "{TAG}"' in text, text
    assert res.mathlib_after == TAG
    assert (d / "lean-toolchain").read_text().strip() == TC


def test_repins_existing_rev():
    d = scratch(
        {
            "lean-toolchain": "leanprover/lean4:v4.29.0\n",
            "lakefile.lean": (
                'require mathlib from git "https://github.com/leanprover-community/mathlib4"'
                ' @ "v4.29.0"\n'
            ),
        }
    )
    bump(d, toolchain=TC, mathlib_tag=TAG)
    text = (d / "lakefile.lean").read_text()
    assert f'@ "{TAG}"' in text and "v4.29.0" not in text


def test_leaves_unrelated_requires_alone():
    d = scratch(
        {
            "lean-toolchain": "leanprover/lean4:v4.29.0\n",
            "lakefile.lean": (
                'require mathlib from git "https://github.com/leanprover-community/mathlib4"'
                ' @ "v4.29.0"\n'
                'require other from git "https://github.com/x/y" @ "main"\n'
            ),
        }
    )
    bump(d, toolchain=TC, mathlib_tag=TAG)
    text = (d / "lakefile.lean").read_text()
    assert 'require other from git "https://github.com/x/y" @ "main"' in text


def test_toml_dialect():
    d = scratch(
        {
            "lean-toolchain": "leanprover/lean4:v4.30.0\n",
            "lakefile.toml": (
                'name = "foo"\n\n'
                "[[require]]\n"
                'name = "mathlib"\n'
                'git = "https://github.com/leanprover-community/mathlib4"\n'
                'rev = "v4.30.0"\n\n'
                "[[require]]\n"
                'name = "other"\n'
                'git = "https://github.com/x/y"\n'
                'rev = "main"\n'
            ),
        }
    )
    bump(d, toolchain=TC, mathlib_tag=TAG)
    text = (d / "lakefile.toml").read_text()
    assert f'rev = "{TAG}"' in text
    assert 'rev = "main"' in text  # unrelated require untouched


def test_toml_adds_rev_when_missing():
    d = scratch(
        {
            "lean-toolchain": "leanprover/lean4:v4.30.0\n",
            "lakefile.toml": (
                "[[require]]\n"
                'name = "mathlib"\n'
                'git = "https://github.com/leanprover-community/mathlib4"\n'
            ),
        }
    )
    bump(d, toolchain=TC, mathlib_tag=TAG)
    assert f'rev = "{TAG}"' in (d / "lakefile.toml").read_text()


def test_manifest_dropped_when_something_changed():
    d = scratch(
        {
            "lean-toolchain": "leanprover/lean4:v4.29.0\n",
            "lake-manifest.json": '{"version":"1.1.0","packages":[]}',
        }
    )
    res = bump(d, toolchain=TC, mathlib_tag=TAG, mathlib_downstream=False)
    assert res.manifest_removed
    assert not (d / "lake-manifest.json").exists()


def test_no_op_when_already_at_target():
    d = scratch({"lean-toolchain": TC + "\n"})
    res = bump(d, toolchain=TC, mathlib_tag=TAG, mathlib_downstream=False)
    assert not res.changed


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
    print(f"\n{'FAILED' if failed else 'all bump tests passed'}")
    sys.exit(1 if failed else 0)
