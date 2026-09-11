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
          inputs = import {ROOT}/pnix/resolver/resolve.nix {{
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
    expr = ROOT / "tests" / "_override_expr.nix"
    expr.write_text(_expr(attr))
    # Inherit the real environment -- nix-instantiate has to be on PATH -- and
    # control only the variables under test.
    env = dict(os.environ)
    env.pop("PNIX_OVERRIDE", None)
    env.update(extra or {})
    if env_value is not None:
        env["PNIX_OVERRIDE"] = env_value
    try:
        return subprocess.run(
            ["nix-instantiate", "--eval", "--strict", "--json", str(expr),
             *NO_EXPERIMENTAL],
            capture_output=True, text=True, check=False, env=env,
        )
    finally:
        expr.unlink(missing_ok=True)


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
        ("dep=relative/path", "not an absolute path"),
        ("dep=/definitely/not/here", "no such directory"),
    ],
)
def test_a_bad_override_throws_rather_than_being_ignored(value, expected):
    """An override that silently did nothing is the worst outcome for a
    variable whose whole purpose is 'use my working tree instead'."""
    proc = _eval("dep.marker", value)
    assert proc.returncode != 0
    assert expected in proc.stderr


# --- pins.local.nix --------------------------------------------------------

def _local_expr(attrpath: str, local: Path | None) -> str:
    arg = f"localFile = {local};" if local else "localFile = null;"
    return textwrap.dedent(f"""
        let
          inputs = import {ROOT}/pnix/resolver/resolve.nix {{
            lockFile = {LOCK};
            {arg}
            overrides = {{
              consumer = {FLAKES}/consumer;
              dep = {FLAKES}/dep;
              plain = {FLAKES}/notaflake;
              sub = {FLAKES}/mono;
            }};
          }};
        in inputs.{attrpath}
    """)


def _eval_local(attr: str, local: Path | None, env_value: str | None = None):
    expr = ROOT / "tests" / "_local_expr.nix"
    expr.write_text(_local_expr(attr, local))
    env = dict(os.environ)
    env.pop("PNIX_OVERRIDE", None)
    if env_value is not None:
        env["PNIX_OVERRIDE"] = env_value
    try:
        return subprocess.run(
            ["nix-instantiate", "--eval", "--strict", "--json", str(expr),
             *NO_EXPERIMENTAL],
            capture_output=True, text=True, check=False, env=env,
        )
    finally:
        expr.unlink(missing_ok=True)


def test_a_local_file_overrides_the_lock(tmp_path):
    local = tmp_path / "pins.local.nix"
    local.write_text(f'{{ dep = {FLAKES}/mono/sub; }}\n')
    proc = _eval_local("dep.marker", local)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == "SUB"


def test_an_absolute_path_string_works_too(tmp_path):
    local = tmp_path / "pins.local.nix"
    local.write_text(f'{{ dep = "{FLAKES}/mono/sub"; }}\n')
    proc = _eval_local("dep.marker", local)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == "SUB"


def test_a_missing_local_file_is_simply_absent(tmp_path):
    proc = _eval_local("dep.marker", tmp_path / "pins.local.nix")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == "DEP"


def test_the_environment_wins_over_the_local_file(tmp_path):
    """Narrower scope wins: the file is this machine, the variable is this run."""
    local = tmp_path / "pins.local.nix"
    local.write_text(f'{{ dep = {FLAKES}/dep; }}\n')
    proc = _eval_local("dep.marker", local, env_value=f"dep={FLAKES}/mono/sub")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == "SUB"


def test_a_typo_in_the_local_file_throws(tmp_path):
    local = tmp_path / "pins.local.nix"
    local.write_text(f'{{ depp = {FLAKES}/dep; }}\n')
    proc = _eval_local("dep.marker", local)
    assert proc.returncode != 0
    assert "is not a pin" in proc.stderr and "depp" in proc.stderr


def test_a_local_path_that_does_not_exist_throws(tmp_path):
    local = tmp_path / "pins.local.nix"
    local.write_text('{ dep = "/definitely/not/here"; }\n')
    proc = _eval_local("dep.marker", local)
    assert proc.returncode != 0
    assert "no such directory" in proc.stderr


def test_it_says_which_inputs_the_local_file_overrode(tmp_path):
    local = tmp_path / "pins.local.nix"
    local.write_text(f'{{ dep = {FLAKES}/mono/sub; }}\n')
    proc = _eval_local("dep.marker", local)
    assert "pins.local.nix" in proc.stderr and "dep" in proc.stderr
