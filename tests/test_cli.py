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
    assert "[1/1]" in err and "foo" in err and "new" in err
    assert "1 pin resolved -- 1 new" in err


def test_the_three_states_are_named_not_inferred(fake_project, capsys):
    """`new`, `updated`, `unchanged`. Not `ahead` or `diverged`: a pin has no
    upstream to be ahead *of*, and saying more would mean a commit-graph walk
    per pin."""
    cli.main(["--project", str(fake_project), "update"])
    assert "new" in capsys.readouterr().err

    cli.main(["--project", str(fake_project), "update"])
    assert "unchanged" in capsys.readouterr().err


def test_the_download_line_is_behind_verbose(fake_project, capsys):
    """With 24 pins the counter already shows the run moving, and two lines per
    pin from eight threads buries the results. It earns its place on a run of
    one or two slow pins, where nothing else moves for 15 s."""
    cli.main(["--project", str(fake_project), "update"])
    assert "fetching foo" not in capsys.readouterr().err


def test_verbose_announces_the_download_before_it_starts(fake_project, capsys):
    """The line has to come *before* the slow part or it reports nothing while
    the time is actually passing."""
    cli.main(["--project", str(fake_project), "update", "-v"])
    err = capsys.readouterr().err
    assert err.index("fetching foo") < err.index("[1/1]")


def test_a_pin_that_did_not_move_says_so(fake_project, capsys):
    cli.main(["--project", str(fake_project), "update"])
    capsys.readouterr()
    cli.main(["--project", str(fake_project), "update", "-v"])
    err = capsys.readouterr().err
    assert "unchanged" in err
    assert "1 unchanged" in err
    # An unchanged pin is never downloaded, even asked verbosely.
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


# --- commands that cannot do what they were asked ---------------------------

def test_updating_a_pin_that_is_not_declared_is_an_error(fake_project, capsys):
    """It resolved nothing, kept every existing entry and exited 0 -- a typo
    looked exactly like a successful no-op."""
    assert cli.main(["--project", str(fake_project), "update", "fooo"]) == 2
    err = capsys.readouterr().err
    assert "not a declared pin" in err and "known: foo" in err


def test_a_bad_name_does_not_touch_the_lock(fake_project):
    cli.main(["--project", str(fake_project), "update"])
    before = (fake_project / cli.LOCK_NAME).read_text()
    assert cli.main(["--project", str(fake_project), "update", "nope"]) == 2
    assert (fake_project / cli.LOCK_NAME).read_text() == before


def test_pruning_says_what_it_removes(fake_project, capsys, monkeypatch):
    """The destructive half of `update`, and it used to be the silent one:
    scoping a run with `--root` to a file that no longer holds every
    declaration drops the rest, printing nothing at all when the scoped set is
    empty."""
    cli.main(["--project", str(fake_project), "update"])
    capsys.readouterr()

    # The fixture stubs the collector, so emptying the declarations means
    # replacing that stub rather than editing a file.
    monkeypatch.setattr("pnix.collect.collect",
                        lambda files, attr="pins": ({}, {}, []))
    assert cli.main(["--project", str(fake_project), "update"]) == 0
    err = capsys.readouterr().err
    assert "foo: locked but no longer declared -- removing" in err
    assert lock.read(fake_project / cli.LOCK_NAME) == {}


def test_a_named_update_does_not_report_pruning(fake_project, capsys):
    """`update foo` keeps every other entry by design, so nothing is removed
    and saying so would be a lie."""
    cli.main(["--project", str(fake_project), "update"])
    capsys.readouterr()
    cli.main(["--project", str(fake_project), "update", "foo"])
    assert "no longer declared" not in capsys.readouterr().err


def test_version_is_reportable(capsys):
    """A pnix older than the lock refuses it outright, so `which pnix is this`
    is the first question when that happens."""
    from pnix import __version__

    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


# --- --exclude --------------------------------------------------------------

def test_exclude_holds_a_pin_at_its_locked_revision(fake_project, capsys,
                                                     monkeypatch):
    cli.main(["--project", str(fake_project), "update"])
    before = lock.read(fake_project / cli.LOCK_NAME)["foo"]["rev"]
    capsys.readouterr()

    # Upstream moves; the exclusion must mean pnix never asks.
    def boom(url, ref=None):
        raise AssertionError("resolved an excluded pin")

    monkeypatch.setattr("pnix.refs.resolve", boom)
    assert cli.main(["--project", str(fake_project), "update",
                     "--exclude", "foo"]) == 0
    assert lock.read(fake_project / cli.LOCK_NAME)["foo"]["rev"] == before
    assert "foo: excluded, keeping" in capsys.readouterr().err


def test_excluding_a_name_that_is_not_declared_is_an_error(fake_project):
    """Sharper than the same mistake in a positional name: a misspelled
    exclusion updates the very pin it was meant to hold still."""
    assert cli.main(["--project", str(fake_project), "update",
                     "--exclude", "fooo"]) == 2


def test_excluding_a_pin_that_was_never_locked_is_an_error(fake_project, capsys):
    """Holding a pin still means keeping the entry it has. One with no entry
    would simply be dropped -- the opposite of what the flag is for."""
    assert cli.main(["--project", str(fake_project), "update",
                     "--exclude", "foo"]) == 2
    assert "never locked" in capsys.readouterr().err


def test_exclude_is_repeatable(fake_project, monkeypatch, capsys):
    cli.main(["--project", str(fake_project), "update"])
    capsys.readouterr()
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"foo": {"type": "github", "url": "https://github.com/o/r"},
             "bar": {"type": "github", "url": "https://github.com/o/b"}},
            {"foo": "/decl.nix", "bar": "/decl.nix"},
            [],
        ),
    )
    # `bar` has no entry yet, so only `foo` may be held.
    assert cli.main(["--project", str(fake_project), "update",
                     "--exclude", "foo", "--exclude", "bar"]) == 2
    assert cli.main(["--project", str(fake_project), "update",
                     "--exclude", "foo"]) == 0
    assert set(lock.read(fake_project / cli.LOCK_NAME)) == {"foo", "bar"}


# --- a lock entry that update used to freeze --------------------------------
#
# Reported in the wild: a nixpkgs pin whose entry had a hash and a rev but no
# `lastModified` built as `nixos-system-...-26.11.19700101.eaad089` forever,
# and every `pnix update` said "unchanged". `_unchanged` only checks the fetch
# fields, so the entry was returned verbatim and prefetch never ran again.

def test_update_refills_an_entry_missing_what_prefetch_produces(fake_project,
                                                                 capsys):
    cli.main(["--project", str(fake_project), "update"])
    path = fake_project / cli.LOCK_NAME
    entry = lock.read(path)["foo"]
    assert "lastModified" in entry

    lock.write(path, {"foo": {k: v for k, v in entry.items()
                              if k != "lastModified"}})
    capsys.readouterr()

    cli.main(["--project", str(fake_project), "update"])
    healed = lock.read(path)["foo"]
    assert healed["lastModified"] == entry["lastModified"]
    assert healed["rev"] == entry["rev"], "the rev must not move to repair it"
    assert "repaired" in capsys.readouterr().err


def test_adding_a_carried_field_reaches_the_lock(fake_project, capsys,
                                                  monkeypatch):
    """`_unchanged` compares the *fetch* fields, which are precisely the ones
    CARRIED_FIELDS are not -- so an edit adding only `excludeFollow` or `dir`
    was reported unchanged and never written, and the declaration silently had
    no effect. For `dir` that means a flake kept being looked for in the wrong
    directory with nothing to show for it."""
    cli.main(["--project", str(fake_project), "update"])
    assert "dir" not in lock.read(fake_project / cli.LOCK_NAME)["foo"]
    capsys.readouterr()

    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"foo": {"type": "github", "url": "https://github.com/o/r",
                     "ref": "main", "dir": "sub",
                     "excludeFollow": ["nixpkgs"]}},
            {"foo": "/decl.nix"}, [],
        ),
    )
    cli.main(["--project", str(fake_project), "update"])
    entry = lock.read(fake_project / cli.LOCK_NAME)["foo"]
    assert entry["dir"] == "sub" and entry["excludeFollow"] == ["nixpkgs"]
    assert "relocked" in capsys.readouterr().err


def test_removing_a_carried_field_also_reaches_the_lock(fake_project,
                                                         monkeypatch):
    """The other direction: dropping `excludeFollow` must stop excluding."""
    decl = {"type": "github", "url": "https://github.com/o/r", "ref": "main",
            "excludeFollow": ["nixpkgs"]}
    monkeypatch.setattr("pnix.collect.collect",
                        lambda files, attr="pins": ({"foo": decl},
                                                    {"foo": "/decl.nix"}, []))
    cli.main(["--project", str(fake_project), "update"])
    assert lock.read(fake_project / cli.LOCK_NAME)["foo"]["excludeFollow"]

    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"foo": {k: v for k, v in decl.items() if k != "excludeFollow"}},
            {"foo": "/decl.nix"}, [],
        ),
    )
    cli.main(["--project", str(fake_project), "update"])
    assert "excludeFollow" not in lock.read(fake_project / cli.LOCK_NAME)["foo"]


def test_repaired_means_only_a_refilled_entry(fake_project, capsys):
    """A routine declaration edit must not be reported under a word that says
    something was broken."""
    cli.main(["--project", str(fake_project), "update"])
    capsys.readouterr()
    path = fake_project / cli.LOCK_NAME
    entry = lock.read(path)["foo"]
    lock.write(path, {"foo": {k: v for k, v in entry.items()
                              if k != "lastModified"}})
    cli.main(["--project", str(fake_project), "update"])
    err = capsys.readouterr().err
    assert "repaired" in err and "relocked" not in err


def test_every_fetching_source_declares_a_hash_in_prefetch_keys():
    """A source that produces fields but declares none can never be repaired."""
    from pnix import sources

    for name, src in sources.SOURCES.items():
        if {"tarball", "file"} & set(src.kinds):
            assert "hash" in set(getattr(src, "prefetch_keys", ())), name
