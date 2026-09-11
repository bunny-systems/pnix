import json

import pytest

from pnix import cli, lock


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"foo": {"type": "github", "url": "https://github.com/o/r",
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
    doc = json.loads((fake_project / cli.LOCK_NAME).read_text())
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
    p = fake_project / cli.LOCK_NAME
    lock.write(p, {"stale": {"type": "github", "rev": "9" * 40}})
    cli.main(["--project", str(fake_project), "update"])
    assert "stale" not in lock.read(p)


def test_update_carries_bookkeeping_fields_into_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"foo": {"type": "github", "url": "https://github.com/o/r",
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
    assert lock.read(tmp_path / cli.LOCK_NAME)["foo"]["excludeFollow"] == ["nixpkgs"]


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
    assert not (fake_project / cli.LOCK_NAME).exists()


def test_look_is_quiet_when_nothing_moved(fake_project, capsys):
    cli.main(["--project", str(fake_project), "update"])
    capsys.readouterr()
    cli.main(["--project", str(fake_project), "look"])
    assert "all pins current" in capsys.readouterr().out


def test_init_vendors_the_resolver(tmp_path, capsys):
    assert cli.main(["--project", str(tmp_path), "init"]) == 0
    assert (tmp_path / ".pnix" / "eval" / "resolve.nix").exists()
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


# --- project discovery -----------------------------------------------------

def test_the_project_is_the_nearest_directory_holding_dot_pnix(tmp_path, monkeypatch):
    """`pnix look` from deep in a module tree has to find the repo, the way
    `git status` does. Defaulting to the working directory instead means
    `modules/` is treated as the project: no lock, every pin "not locked yet"."""
    (tmp_path / ".pnix").mkdir()
    deep = tmp_path / "modules" / "features" / "desktop"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    assert cli.find_project(None) == tmp_path.resolve()


def test_an_explicit_project_is_never_searched_upward(tmp_path, monkeypatch):
    """If you named a directory, you meant that directory -- even one with no
    .pnix/ yet, which is what `init` needs."""
    (tmp_path / ".pnix").mkdir()
    inner = tmp_path / "inner"
    inner.mkdir()
    monkeypatch.chdir(tmp_path)
    assert cli.find_project(inner) == inner


def test_outside_a_project_it_is_a_usage_error_not_a_traceback(tmp_path, monkeypatch,
                                                               capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["look"]) == 2
    assert "no .pnix/" in capsys.readouterr().err


def test_init_works_where_no_project_exists_yet(tmp_path, monkeypatch):
    """The one command that must not require a .pnix/ to already be there."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 0
    assert (tmp_path / ".pnix" / "default.nix").exists()


# --- progress --------------------------------------------------------------
#
# `pnix update` on a real config is ~20 s of network. Reporting nothing until
# it finished was indistinguishable from a hang.

def test_update_reports_each_pin_as_it_lands(fake_project, capsys):
    cli.main(["--project", str(fake_project), "update"])
    err = capsys.readouterr().err
    assert "resolving 1 pin" in err
    assert "[1/1] foo: new -> " in err
    assert "1 pin resolved, 1 changed" in err


def test_update_announces_the_download_before_it_starts(fake_project, capsys):
    """The line has to come *before* the slow part or it reports nothing while
    the time is actually passing."""
    cli.main(["--project", str(fake_project), "update"])
    err = capsys.readouterr().err
    assert err.index("fetching foo") < err.index("[1/1] foo")


def test_a_pin_that_did_not_move_says_so_and_is_not_counted(fake_project, capsys):
    cli.main(["--project", str(fake_project), "update"])
    capsys.readouterr()
    cli.main(["--project", str(fake_project), "update"])
    err = capsys.readouterr().err
    assert "foo: unchanged" in err
    assert "0 changed" in err
    assert "fetching foo" not in err


def test_quiet_prints_no_progress(fake_project, capsys):
    cli.main(["--project", str(fake_project), "update", "-q"])
    err = capsys.readouterr().err
    assert "resolving" not in err and "[1/1]" not in err


def test_progress_goes_to_stderr_so_stdout_stays_clean(fake_project, capsys):
    cli.main(["--project", str(fake_project), "update"])
    assert capsys.readouterr().out == ""


def test_look_stays_quiet_by_default(fake_project, capsys):
    """`look` is a report; its output is the point, so progress would bury it."""
    cli.main(["--project", str(fake_project), "look"])
    err = capsys.readouterr().err
    assert "resolving" not in err
