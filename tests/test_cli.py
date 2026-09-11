import json

import pytest

from pnix import cli, lock


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"foo": {"type": "github", "owner": "o", "repo": "r",
                     "ref": "main"}},
            {"foo": "/decl.nix"},
            [],
        ),
    )
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "1" * 40)
    monkeypatch.setattr("pnix.prefetch.tarball",
                        lambda url: ("sha256-AAA", 1788914643))
    monkeypatch.setattr("pnix.discover.candidates",
                        lambda roots, attr="pins": [tmp_path / "decl.nix"])
    return tmp_path


def test_update_writes_a_lock(fake_project):
    rc = cli.main(["--project", str(fake_project), "update"])
    assert rc == 0
    doc = json.loads((fake_project / "pins.lock.json").read_text())
    assert doc["pins"]["foo"]["rev"] == "1" * 40
    assert doc["pins"]["foo"]["hash"] == "sha256-AAA"
    # The epoch only. The date string is derived by the resolver, because an
    # upstream flake.lock records lastModified and never the date.
    assert doc["pins"]["foo"]["lastModified"] == 1788914643
    assert "lastModifiedDate" not in doc["pins"]["foo"]
    assert doc["pins"]["foo"]["fetch"] == {
        "kind": "tarball",
        "url": f"https://github.com/o/r/archive/{'1' * 40}.tar.gz",
        "hash": "sha256-AAA",
    }


def test_update_is_idempotent_and_skips_prefetch(fake_project, monkeypatch):
    cli.main(["--project", str(fake_project), "update"])
    calls = []
    monkeypatch.setattr("pnix.prefetch.tarball",
                        lambda url: (calls.append(url), ("sha256-AAA", 1))[1])
    cli.main(["--project", str(fake_project), "update"])
    assert calls == []


def test_update_prunes_pins_no_longer_declared(fake_project):
    p = fake_project / "pins.lock.json"
    lock.write(p, {"stale": {"type": "github", "rev": "9" * 40}})
    cli.main(["--project", str(fake_project), "update"])
    assert "stale" not in lock.read(p)


def test_update_carries_bookkeeping_fields_into_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"foo": {"type": "github", "owner": "o", "repo": "r",
                     "excludeFollow": ["nixpkgs"]}},
            {"foo": "/decl.nix"},
            [],
        ),
    )
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "1" * 40)
    monkeypatch.setattr("pnix.prefetch.tarball",
                        lambda url: ("sha256-AAA", 1788914643))
    monkeypatch.setattr("pnix.discover.candidates",
                        lambda roots, attr="pins": [tmp_path / "decl.nix"])
    cli.main(["--project", str(tmp_path), "update"])
    assert lock.read(tmp_path / "pins.lock.json")["foo"]["excludeFollow"] == ["nixpkgs"]


def test_look_reports_moved_pins(fake_project, capsys, monkeypatch):
    cli.main(["--project", str(fake_project), "update"])
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "2" * 40)
    rc = cli.main(["--project", str(fake_project), "look"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "foo" in out and "2" * 8 in out


def test_look_does_not_download(fake_project, capsys, monkeypatch):
    """resolve is one ls-remote; prefetch is a download. Drift only needs the
    rev, so `look` must never reach for the hash."""
    cli.main(["--project", str(fake_project), "update"])
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "2" * 40)

    def boom(url):
        raise AssertionError("look prefetched")

    monkeypatch.setattr("pnix.prefetch.tarball", boom)
    assert cli.main(["--project", str(fake_project), "look"]) == 0


def test_look_does_not_write_a_lock(fake_project):
    cli.main(["--project", str(fake_project), "look"])
    assert not (fake_project / "pins.lock.json").exists()


def test_look_is_quiet_when_nothing_moved(fake_project, capsys):
    cli.main(["--project", str(fake_project), "update"])
    capsys.readouterr()
    cli.main(["--project", str(fake_project), "look"])
    assert "all pins current" in capsys.readouterr().out


def test_init_vendors_the_resolver(tmp_path, capsys):
    assert cli.main(["--project", str(tmp_path), "init"]) == 0
    assert (tmp_path / "nix" / "pins" / "resolve.nix").exists()
    assert "wrote" in capsys.readouterr().out


def test_update_reports_skipped_candidates(tmp_path, monkeypatch, capsys):
    """A file the user expected to declare pins, silently skipped, is the
    failure mode this line exists to prevent."""
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": ({}, {}, ["/some/package.nix"]),
    )
    monkeypatch.setattr("pnix.discover.candidates",
                        lambda roots, attr="pins": [tmp_path / "package.nix"])
    cli.main(["--project", str(tmp_path), "update"])
    err = capsys.readouterr().err
    assert "skipped 1 of 1" in err and "/some/package.nix" in err
