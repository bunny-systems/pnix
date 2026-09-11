import json
from pathlib import Path

import pytest

from pnix import lock


def test_write_then_read_round_trips(tmp_path: Path):
    pins = {
        "nixpkgs": {"type": "github", "owner": "NixOS", "repo": "nixpkgs",
                    "rev": "a" * 40, "hash": "sha256-AAAA"},
    }
    p = tmp_path / "pins.lock.json"
    lock.write(p, pins)
    assert lock.read(p) == pins


def test_write_emits_schema_and_sorted_keys(tmp_path: Path):
    p = tmp_path / "pins.lock.json"
    lock.write(p, {"zeta": {"type": "git"}, "alpha": {"type": "git"}})
    raw = p.read_text()
    doc = json.loads(raw)
    assert doc["schema"] == lock.SCHEMA
    assert list(doc["pins"].keys()) == ["alpha", "zeta"]
    assert raw.endswith("\n")


def test_read_rejects_future_schema(tmp_path: Path):
    p = tmp_path / "pins.lock.json"
    p.write_text(json.dumps({"schema": 99, "pins": {}}))
    try:
        lock.read(p)
    except lock.SchemaError as e:
        assert "99" in str(e)
    else:
        raise AssertionError("expected SchemaError")


def test_read_missing_file_returns_empty(tmp_path: Path):
    assert lock.read(tmp_path / "nope.json") == {}


# --- schema migration ------------------------------------------------------
#
# The regression these guard: `read` used to raise on any schema mismatch, so
# the only way past a bump was to delete the lock -- which re-resolves every
# pin that named no explicit `rev` to whatever is at the tip today. A version
# number must never be able to unpin a fleet.

def _write_raw(path, version, pins):
    path.write_text(json.dumps({"schema": version, "pins": pins}) + "\n")


PINNED = {
    "nixpkgs": {
        "type": "github", "owner": "NixOS", "repo": "nixpkgs",
        "rev": "a" * 40, "hash": "sha256-AAA", "lastModified": 1788914643,
        "fetch": {"kind": "tarball", "url": "https://x/a.tar.gz",
                  "hash": "sha256-AAA"},
    },
}


@pytest.mark.parametrize("version", [2, 3])
def test_an_older_lock_is_migrated_not_discarded(tmp_path, version):
    p = tmp_path / "pins.lock.json"
    _write_raw(p, version, PINNED)
    pins, came_from = lock.read_at(p)
    assert came_from == version
    assert pins == PINNED, "migration must not touch a single locked field"


def test_migration_preserves_the_rev_of_every_pin(tmp_path):
    """The whole point: revs survive a bump untouched."""
    p = tmp_path / "pins.lock.json"
    _write_raw(p, 2, PINNED)
    assert lock.read(p)["nixpkgs"]["rev"] == "a" * 40


def test_schema_1_gains_a_fetch_node(tmp_path):
    """Schema 1 keyed the resolver on `type`, which is what a source class turns
    into a `fetch` node -- so it is derivable, not a reason to re-resolve."""
    p = tmp_path / "pins.lock.json"
    node = {k: v for k, v in PINNED["nixpkgs"].items() if k != "fetch"}
    _write_raw(p, 1, {"nixpkgs": node})
    pins, came_from = lock.read_at(p)
    assert came_from == 1
    assert pins["nixpkgs"]["fetch"]["kind"] == "tarball"
    assert pins["nixpkgs"]["rev"] == "a" * 40


def test_a_newer_lock_is_still_refused(tmp_path):
    """Its fields may mean something this pnix does not know."""
    p = tmp_path / "pins.lock.json"
    _write_raw(p, lock.SCHEMA + 1, PINNED)
    with pytest.raises(lock.SchemaError) as e:
        lock.read(p)
    assert "upgrade pnix" in str(e.value)


def test_a_file_with_no_schema_is_refused(tmp_path):
    p = tmp_path / "pins.lock.json"
    p.write_text('{"pins": {}}\n')
    with pytest.raises(lock.SchemaError) as e:
        lock.read(p)
    assert "no `schema` field" in str(e.value)


def test_a_current_lock_reports_no_migration(tmp_path):
    p = tmp_path / "pins.lock.json"
    lock.write(p, PINNED)
    assert lock.read_at(p) == (PINNED, None)


def test_every_schema_below_current_has_a_migration():
    """A bump with no entry here is a bump that cannot be crossed."""
    assert set(lock.MIGRATIONS) == set(range(1, lock.SCHEMA))
