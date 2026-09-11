"""Nix-level tests. Each tests/nix/<name>.nix evaluates to a list of
{ name, expr, expected }; this driver evaluates them and reports failures.

Note the driver passes no `lib`: nothing under pnix/ is allowed to want it.
"""

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NO_EXPERIMENTAL = ["--option", "experimental-features", ""]

DRIVER = """
{ file }:
let cases = import file { };
in map (c: { inherit (c) name; ok = c.expr == c.expected; }) cases
"""


def run_nix_cases(nix_file: Path) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        expr = Path(tmp) / "_driver.nix"
        expr.write_text(DRIVER)
        proc = subprocess.run(
            ["nix-instantiate", "--eval", "--strict", "--json", str(expr),
             "--arg", "file", str(nix_file), *NO_EXPERIMENTAL],
            capture_output=True, text=True, check=False,
        )
    if proc.returncode != 0:
        pytest.fail(f"nix eval failed for {nix_file}:\n{proc.stderr}")
    return json.loads(proc.stdout)


def check(name: str):
    for case in run_nix_cases(ROOT / "tests" / "nix" / f"{name}.nix"):
        assert case["ok"], f"{name} case failed: {case['name']}"


def test_collect_cases():
    check("collect")


def test_no_lib_under_nix():
    """lib lives in nixpkgs; requiring it would mean fetching nixpkgs before
    pnix can read a single declaration. tack and npins are both pure-builtins
    for this reason."""
    offenders = []
    for tree in ("pnix/resolver", "pnix/nixsrc"):
        for f in (ROOT / tree).rglob("*.nix"):
            if "lib." in f.read_text():
                offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, f"Nix files referencing lib: {offenders}"


def test_fetchers_cases():
    check("fetchers")


def test_every_source_kind_has_a_fetcher():
    """`fetch.kind` is the contract; the two halves must not drift.

    Stronger than matching filenames to source types: several sources map onto
    one primitive, which is the whole point of normalizing. What must hold is
    that no source can emit a kind the vendored resolver cannot fetch.
    """
    from pnix import sources
    have = {p.stem for p in (ROOT / "pnix" / "resolver" / "fetchers").glob("*.nix")}
    have.discard("default")
    emitted = {k for src in sources.SOURCES.values() for k in src.kinds}
    assert emitted <= have, f"sources emit kinds with no fetcher: {emitted - have}"


def test_fetch_spec_round_trips_through_the_lock(tmp_path):
    """What a source emits must be what a fetcher accepts."""
    from pnix import sources
    spec = sources.get("github").fetch_spec({
        "type": "github", "host": "github.com", "owner": "o", "repo": "r",
        "rev": "a" * 40, "hash": "sha256-AAA",
    })
    assert spec == {
        "kind": "tarball",
        "url": f"https://github.com/o/r/archive/{'a' * 40}.tar.gz",
        "hash": "sha256-AAA",
    }
    git = sources.get("git").fetch_spec({
        "type": "git", "url": "u", "rev": "b" * 40, "submodules": True,
    })
    assert git == {"kind": "git", "url": "u", "rev": "b" * 40, "submodules": True}


def test_flake_cases():
    check("flake")


def test_follows_cases():
    check("follows")


def test_resolve_cases():
    check("resolve")


def test_date_cases():
    check("date")


def test_the_two_halves_agree_on_the_lock_schema():
    """`lock.py` writes it and the vendored resolver checks it. They are
    separate files by necessity -- one ships with the CLI, one is copied into
    the consumer -- so nothing but a test keeps them in step.
    """
    import re

    from pnix import lock

    text = (ROOT / "pnix" / "resolver" / "resolve.nix").read_text()
    m = re.search(r"SCHEMA\s*=\s*(\d+)\s*;", text)
    assert m, "resolve.nix declares no SCHEMA"
    assert int(m.group(1)) == lock.SCHEMA, (
        f"resolve.nix speaks schema {m.group(1)}, lock.py writes {lock.SCHEMA}"
    )


def test_patch_cases():
    check("patch")
