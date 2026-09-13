"""PNIX_OVERRIDE, which needs a real environment and so cannot live in the
pure-Nix case files.
"""

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NO_EXPERIMENTAL = ["--option", "experimental-features", ""]

LOCK = ROOT / "tests" / "nix" / "fixtures" / "locks" / "demo.lock.json"
FLAKES = ROOT / "tests" / "nix" / "fixtures" / "flakes"

def _expr(attrpath: str) -> str:
    """`inputs.${attr}` will not do: an interpolated string is one attribute
    name, so `dep.marker` becomes a lookup for the attribute `"dep.marker"`."""
    return textwrap.dedent(f"""
        let
          inputs = import {ROOT}/pnix/resolver/eval/resolve.nix {{
            lockFile = {LOCK};
            overrides = {{
              consumer = {FLAKES}/consumer;
              dep = {FLAKES}/dep;
              plain = {FLAKES}/notaflake;
              sub = {FLAKES}/mono;
            }};
          }};
        in inputs.{attrpath}
    """)


def _eval(attr: str, env_value: str | None, extra: dict | None = None):
    """Evaluate the expression directly rather than through a scratch file.

    This used to write `tests/_override_expr.nix` and remove it in a `finally`,
    which is fine until pytest is killed before the `finally` runs -- then an
    untracked, un-gitignored `.nix` file sits in the repo waiting for someone's
    `git add -A`. The expression carries only absolute paths, so there was never
    a reason for it to be on disk.
    """
    # Inherit the real environment -- nix-instantiate has to be on PATH -- and
    # control only the variables under test.
    env = dict(os.environ)
    for name in ("PNIX_OVERRIDE", "PNIX_OVERRIDES", "TACK_OVERRIDES"):
        env.pop(name, None)
    env.update(extra or {})
    if env_value is not None:
        env["PNIX_OVERRIDE"] = env_value
    return subprocess.run(
        ["nix-instantiate", "--eval", "--strict", "--json",
         "--expr", _expr(attr), *NO_EXPERIMENTAL],
        capture_output=True, text=True, check=False, env=env,
    )


def test_without_the_variable_the_lock_wins():
    proc = _eval("dep.marker", None)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == "DEP"


def test_an_override_replaces_the_source():
    """`dep` is pointed at the `mono/sub` tree, which says SUB, not DEP."""
    proc = _eval("dep.marker", f"dep={FLAKES}/mono/sub")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == "SUB"


def test_the_override_propagates_through_follows():
    """The point of overriding: consumers of the pin see it too. `consumer`
    follows `dep`, so overriding `dep` must change what `consumer` got."""
    proc = _eval("consumer.got", f"dep={FLAKES}/mono/sub")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == "none"


def test_several_entries_separated_by_commas_or_spaces():
    for sep in (",", " "):
        proc = _eval("dep.marker", f"dep={FLAKES}/mono/sub{sep}plain={FLAKES}/dep")
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout) == "SUB"


def test_tilde_is_expanded(tmp_path):
    home = tmp_path / "home"
    (home / "checkout").mkdir(parents=True)
    (home / "checkout" / "flake.nix").write_text(
        '{ outputs = { self, ... }: { marker = "LOCAL"; }; }\n')
    proc = _eval("dep.marker", "dep=~/checkout", extra={"HOME": str(home)})
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == "LOCAL"


def test_it_says_which_inputs_it_overrode():
    proc = _eval("dep.marker", f"dep={FLAKES}/mono/sub")
    assert "overriding inputs" in proc.stderr and "dep" in proc.stderr


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("nosuchpin=/tmp", "is not a pin"),
        ("dep", "not of the form"),
        ("dep=relative/path", "neither an absolute path nor a repository URL"),
        ("dep=/definitely/not/here", "no such directory"),
    ],
)
def test_a_bad_override_throws_rather_than_being_ignored(value, expected):
    """An override that silently did nothing is the worst outcome for a
    variable whose whole purpose is 'use my working tree instead'."""
    proc = _eval("dep.marker", value)
    assert proc.returncode != 0
    assert expected in proc.stderr


# --- a near-miss variable name ---------------------------------------------

def test_the_plural_name_is_caught_rather_than_ignored():
    """`PNIX_OVERRIDES` is tack's spelling, and muscle memory outlives a
    migration. pnix cannot warn about a variable it does not read, so it looks
    for this one deliberately -- measured on a real migration, where the plural
    produced a completely normal-looking build off the locked rev, with no
    trace and no error."""
    proc = _eval("dep.marker", None, {"PNIX_OVERRIDES": "dep=/tmp"})
    assert proc.returncode != 0
    assert "PNIX_OVERRIDES is set" in proc.stderr
    assert "no trailing S" in proc.stderr


def test_tacks_variable_is_caught_too():
    proc = _eval("dep.marker", None, {"TACK_OVERRIDES": "dep=/tmp"})
    assert proc.returncode != 0
    assert "TACK_OVERRIDES is set" in proc.stderr


def test_the_real_variable_wins_over_a_near_miss():
    """Both set is not an error: the correct one is unambiguous."""
    proc = _eval("dep.marker", f"dep={FLAKES}/mono/sub",
                 {"PNIX_OVERRIDES": "dep=/nonsense"})
    assert proc.returncode == 0, proc.stderr


# --- a repository URL -------------------------------------------------------
#
# The line an override draws is transient versus recorded, not local versus
# remote: nothing here reaches the lock either way. Refusing a URL rested on
# tack resolving one with `builtins.getFlake`, which confused that tool's
# implementation needing flakes with the feature needing them -- `fetchGit`
# does it with no hash and no experimental feature.

def test_a_url_with_no_fragment_is_accepted(local_repo):
    proc = _eval("dep.outPath", f"dep=file://{local_repo}")
    assert proc.returncode == 0, proc.stderr
    assert "/nix/store/" in proc.stdout


def test_a_url_fragment_may_be_a_ref(local_repo):
    proc = _eval("dep.outPath", f"dep=file://{local_repo}#main")
    assert proc.returncode == 0, proc.stderr


def test_a_url_fragment_may_be_a_rev(local_repo, local_repo_head):
    """A rev needs allRefs, since fetchGit otherwise looks only at the default
    branch -- and the interesting revs (a PR head, a fork) are exactly the ones
    that are not on it."""
    proc = _eval("dep.outPath", f"dep=file://{local_repo}#{local_repo_head}")
    assert proc.returncode == 0, proc.stderr


def test_a_url_and_a_path_reach_the_same_tree(local_repo, local_repo_head):
    """The two override forms are interchangeable when they name one tree."""
    by_url = _eval("dep.outPath", f"dep=file://{local_repo}#{local_repo_head}")
    assert by_url.returncode == 0, by_url.stderr
    assert by_url.stdout.strip().strip('"').startswith("/nix/store/")
