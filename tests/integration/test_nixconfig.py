"""The gate. Network and a real ~/nixconfig checkout required.

Run explicitly:  pytest -m network -v -s

Nothing proceeds to patches until every assertion here passes.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pnix import cli, lock

NIXCONFIG = Path(os.path.expanduser("~/nixconfig"))
TACK_LOCK = NIXCONFIG / ".tack" / "pins.lock.json"

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        not TACK_LOCK.exists(),
        reason="~/nixconfig/.tack/pins.lock.json not present",
    ),
]

# tack's lock is the bare pins map; pnix wraps it in a schema envelope.
SUBMODULE_PINS = ("hyprland", "umbriel")


@pytest.fixture(scope="module")
def tack() -> dict:
    return json.loads(TACK_LOCK.read_text())


# `tack look` lines are either "<name>: unchanged" or
# "<name>: <old> -> <new> (ahead|behind|diverged)".
MOVED = re.compile(r"^(\S+): [0-9a-f]+ -> ([0-9a-f]+)")


@pytest.fixture(scope="module")
def tack_look() -> dict[str, str] | None:
    """Pins tack re-resolves to a different rev than its lock records.

    Returns name -> abbreviated new rev, or None when the oracle is unusable.

    `tack look` goes through the GitHub API, which is rate-limited without a
    token -- observed: a handful of runs in one session is enough to turn every
    line into `FAILED: auth: https://api.github.com/...`. A partial answer is
    worse than none here, because the pins it failed on would silently fall
    back to the stale lock and be reported as pnix bugs. So: all or nothing.

    (pnix does not have this problem. `git ls-remote` needs no token and no
    API, which is why it is the whole ref-resolution strategy.)
    """
    if shutil.which("tack") is None:
        return None
    proc = subprocess.run(["tack", "look"], cwd=NIXCONFIG,
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0 or "FAILED" in proc.stdout:
        return None
    moved = {}
    for line in proc.stdout.splitlines():
        m = MOVED.match(line.strip())
        if m:
            moved[m.group(1)] = m.group(2)
    return moved


@pytest.fixture(scope="module")
def locked(tmp_path_factory):
    project = tmp_path_factory.mktemp("gate")
    src = Path(__file__).parent
    (project / "pins.nix").write_text((src / "pins.nix").read_text())
    assert cli.main(["--project", str(project), "update"]) == 0
    return lock.read(project / "pins.lock.json")


def test_every_pin_locked(locked, tack):
    assert set(locked) == set(tack), (
        f"missing: {set(tack) - set(locked)}, extra: {set(locked) - set(tack)}"
    )


def test_revs_match_tack(locked, tack, tack_look):
    """Same declaration, same upstream: the revs must agree.

    Compared against tack's *fresh* resolution, not against its stored lock.
    A stored lock goes stale the moment any upstream branch moves -- measured:
    a 14-hour-old lock had 6 of 23 pins behind -- and a gate that fails for
    that reason tests the clock, not the resolver. `tack look` re-resolves
    every ref through tack's own code path, which is exactly the thing this
    assertion wants to compare against.

    A mismatch here is real: it means pnix picked a different ref than tack
    did, not that upstream moved.
    """
    if tack_look is None:
        pytest.skip(
            "no usable `tack look` output (tack missing, or the GitHub API "
            "rate-limited it); a stored lock alone cannot distinguish a stale "
            "rev from a resolution bug"
        )

    mismatched = {}
    for name in sorted(locked):
        if name not in tack:
            continue
        expected = tack_look.get(name) or tack[name].get("rev")
        got = locked[name].get("rev")
        if expected and got and not got.startswith(expected):
            mismatched[name] = (expected, got)

    assert not mismatched, f"rev mismatches (tack, pnix): {mismatched}"


def test_github_hashes_match_tacks_narhash(locked, tack):
    """The strongest assertion here: same rev is not the same tree.

    tack fetches via fetchTree; pnix fetches the codeload tarball. Both hashes
    are the NAR hash of the unpacked source, so they must agree -- if they do
    not, the two resolvers would produce different store paths from an
    identical lock.
    """
    mismatched = {
        name: (tack[name].get("narHash"), node.get("hash"))
        for name, node in sorted(locked.items())
        if node["type"] == "github"
        and tack.get(name, {}).get("narHash")
        and tack[name]["rev"] == node.get("rev")
        and tack[name]["narHash"] != node.get("hash")
    }
    assert not mismatched, f"NAR hash mismatches (tack, pnix): {mismatched}"


def test_submodule_pins_go_through_fetchgit(locked):
    """Forge tarballs omit submodules, so these must not be type github."""
    for name in SUBMODULE_PINS:
        assert locked[name]["type"] == "git", name
        assert locked[name]["submodules"] is True, name
        assert "hash" not in locked[name], name


def test_every_github_pin_has_an_sri_hash(locked):
    for name, node in locked.items():
        if node["type"] == "github":
            assert node["hash"].startswith("sha256-"), name


def test_excludefollow_survives_into_the_lock(locked):
    """Nine pins deliberately opt out of following. If the CLI drops the
    field, the resolver silently flattens them onto our nixpkgs."""
    opted_out = {n for n, v in locked.items() if v.get("excludeFollow")}
    assert "sops-nix" in opted_out
    assert len(opted_out) >= 9, opted_out
