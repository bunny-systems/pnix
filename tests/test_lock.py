import json
from pathlib import Path

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
