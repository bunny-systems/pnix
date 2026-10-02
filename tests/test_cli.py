import json

import pytest

from pnix import cli, lock, patchhash


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


def test_a_repair_that_produced_nothing_says_so(fake_project, capsys,
                                                 monkeypatch):
    """Reported from the wild twice over: the first run wrote entries with no
    `lastModified`, and the run after the repair landed said "repaired" for
    every one of them while writing back identical entries. A repair that
    filled nothing must not report success."""
    monkeypatch.setattr("pnix.prefetch.tarball", lambda url: ("sha256-AAA", None))
    cli.main(["--project", str(fake_project), "update"])
    err = capsys.readouterr().err
    assert "no lastModified could be determined" in err
    assert "repaired" not in err


def test_a_repair_that_filled_the_gap_is_still_reported(fake_project, capsys):
    cli.main(["--project", str(fake_project), "update"])
    path = fake_project / cli.LOCK_NAME
    entry = lock.read(path)["foo"]
    lock.write(path, {"foo": {k: v for k, v in entry.items()
                              if k != "lastModified"}})
    capsys.readouterr()
    cli.main(["--project", str(fake_project), "update"])
    err = capsys.readouterr().err
    assert "repaired" in err
    assert "could be determined" not in err


def test_update_warns_when_the_vendored_resolver_is_older(fake_project, capsys):
    from pnix import vendor

    vendor.install(fake_project)
    target = fake_project / ".pnix" / "eval" / "resolve.nix"
    target.write_text(target.read_text().replace("patchPkgs", "patchPkgsOld"))

    cli.main(["--project", str(fake_project), "update"])
    err = capsys.readouterr().err
    assert "eval/resolve.nix" in err and "pnix init" in err


def test_update_is_quiet_when_the_vendored_resolver_matches(fake_project, capsys):
    from pnix import vendor

    vendor.install(fake_project)
    cli.main(["--project", str(fake_project), "update"])
    assert "pnix init" not in capsys.readouterr().err


def test_update_does_not_nag_a_project_with_nothing_vendored(fake_project, capsys):
    """`.pnix/` holding only a lock is not an out-of-date resolver."""
    cli.main(["--project", str(fake_project), "update"])
    assert "pnix init" not in capsys.readouterr().err


def test_update_reports_a_patch_whose_pr_has_merged(fake_project, capsys, monkeypatch):
    """`look` said so already; `update` is where people actually find out."""
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"foo": {"type": "github", "url": "https://github.com/o/r",
                     "ref": "main", "patches": [{"pr": 7}]}},
            {"foo": "/decl.nix"},
            [],
        ),
    )
    monkeypatch.setattr(
        "pnix.patches.resolve",
        lambda spec, name, project: [
            {"kind": "pr", "number": 7, "merged": True, "state": "closed",
             "url": "https://x/7.diff", "hash": "sha256-AAA"}
        ],
    )
    cli.main(["--project", str(fake_project), "update"])
    assert "PR #7 is merged upstream" in capsys.readouterr().err


def test_workers_can_be_named(fake_project):
    assert cli.main(["--project", str(fake_project), "update", "--workers", "1"]) == 0
    assert cli.main(["--project", str(fake_project), "look", "--workers", "2"]) == 0


def test_zero_workers_is_refused(fake_project, capsys):
    """ThreadPoolExecutor(max_workers=0) raises ValueError deep in the stdlib;
    refusing it here is the difference between a usage error and a traceback."""
    assert cli.main(["--project", str(fake_project), "update", "--workers", "0"]) == 2
    assert "workers" in capsys.readouterr().err


# --- ref cache -----------------------------------------------------------
#
# These stub `subprocess.run`, not `refs.resolve`: the cache lives inside
# `_ls_remote`, below `resolve`, so stubbing `resolve` would take the code under
# test out of the picture entirely.

@pytest.fixture
def cached_project(tmp_path, monkeypatch):
    """Like `fake_project`, but ref resolution really runs -- against a fake git."""
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"foo": {"type": "github", "url": "https://github.com/o/r",
                     "ref": "main"}},
            {"foo": "/decl.nix"},
            [],
        ),
    )
    monkeypatch.setattr("pnix.prefetch.tarball",
                        lambda url: ("sha256-AAA", 1788914643))
    monkeypatch.setattr("pnix.discover.candidates",
                        lambda roots, attr="pins": [tmp_path / "decl.nix"])
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    calls = []

    class Proc:
        returncode = 0
        stderr = ""
        stdout = f"{'1' * 40}\trefs/heads/main\n"

    def run(cmd, **kw):
        if cmd[:2] == ["git", "ls-remote"]:
            calls.append(cmd)
            return Proc()
        raise AssertionError(f"unexpected subprocess: {cmd}")

    monkeypatch.setattr("subprocess.run", run)
    return tmp_path, calls


def test_a_second_command_reuses_the_cached_rev(cached_project):
    """`look` then `update` is the common pair, and the whole point is that the
    second one does not pay for the refs again."""
    project, calls = cached_project
    cli.main(["--project", str(project), "look"])
    assert len(calls) == 1
    cli.main(["--project", str(project), "look"])
    assert len(calls) == 1, "the second look should have come from the cache"


def test_refresh_bypasses_the_cache(cached_project):
    project, calls = cached_project
    cli.main(["--project", str(project), "look"])
    cli.main(["--project", str(project), "look", "--refresh"])
    assert len(calls) == 2


def test_naming_a_pin_bypasses_the_cache(cached_project):
    """Naming a pin is how you say "that one, fresh"."""
    project, calls = cached_project
    cli.main(["--project", str(project), "update"])
    cli.main(["--project", str(project), "update", "foo"])
    assert len(calls) == 2


def test_look_says_when_an_answer_came_from_cache(cached_project, capsys):
    """A drift reporter may be stale, but never silently: `look` exists to catch
    a moved pin, and a cache hit is exactly when it would miss one."""
    project, _ = cached_project
    cli.main(["--project", str(project), "look"])
    capsys.readouterr()
    cli.main(["--project", str(project), "look"])
    out = capsys.readouterr()
    assert "cache" in (out.out + out.err).lower()
    assert "--refresh" in (out.out + out.err)


def test_update_does_not_announce_the_cache(cached_project, capsys):
    """For `update` a cached rev is a real rev, just not the newest, so it is
    used silently -- the opposite of `look`."""
    project, _ = cached_project
    cli.main(["--project", str(project), "update"])
    capsys.readouterr()
    cli.main(["--project", str(project), "update"])
    out = capsys.readouterr()
    assert "--refresh" not in (out.out + out.err)


# --- sitting 2: exit code, init output ------------------------------------

def test_look_still_exits_zero_on_drift_by_default(fake_project, monkeypatch):
    cli.main(["--project", str(fake_project), "update"])
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "2" * 40)
    assert cli.main(["--project", str(fake_project), "look", "--refresh"]) == 0


def test_exit_code_makes_drift_a_failure(fake_project, monkeypatch):
    """So CI can gate on it, which is the main reason to run `look` at all."""
    cli.main(["--project", str(fake_project), "update"])
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "2" * 40)
    assert cli.main(
        ["--project", str(fake_project), "look", "--exit-code", "--refresh"]) == 1


def test_exit_code_is_zero_when_nothing_moved(fake_project):
    cli.main(["--project", str(fake_project), "update"])
    assert cli.main(["--project", str(fake_project), "look", "--exit-code"]) == 0


def test_init_reports_only_what_it_changed(tmp_path, capsys):
    cli.main(["--project", str(tmp_path), "init"])
    capsys.readouterr()
    target = tmp_path / ".pnix" / "eval" / "resolve.nix"
    target.write_text(target.read_text().replace("patchPkgs", "patchPkgsOld"))
    cli.main(["--project", str(tmp_path), "init"])
    out = capsys.readouterr().out
    assert "eval/resolve.nix" in out
    assert "eval/date.nix" not in out


def test_init_says_nothing_changed_rather_than_listing_everything(tmp_path, capsys):
    cli.main(["--project", str(tmp_path), "init"])
    capsys.readouterr()
    assert cli.main(["--project", str(tmp_path), "init"]) == 0
    out = capsys.readouterr().out
    assert "up to date" in out.lower()
    assert "wrote" not in out


def test_look_aligns_its_name_column(fake_project, monkeypatch, capsys):
    """`update` has had a width-aligned column since it was written; `look`
    printed a ragged `name: ...` next to it."""
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"a": {"type": "github", "url": "https://github.com/o/a", "ref": "main"},
             "a-much-longer-name": {"type": "github",
                                    "url": "https://github.com/o/b", "ref": "main"}},
            {"a": "/decl.nix", "a-much-longer-name": "/decl.nix"},
            [],
        ),
    )
    cli.main(["--project", str(fake_project), "look"])
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "not locked" in ln]
    assert len(lines) == 2
    assert len({ln.index("not locked") for ln in lines}) == 1, lines


def test_a_migrated_lock_names_both_commands_to_run(fake_project, capsys):
    p = fake_project / cli.LOCK_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": 4, "pins": {"foo": {"rev": "9" * 40}}}))
    cli.main(["--project", str(fake_project), "update"])
    err = capsys.readouterr().err
    assert "pnix update" in err and "pnix init" in err
    assert "one commit" in err
    assert json.loads(p.read_text())["schema"] == 5


@pytest.fixture
def patched_project(tmp_path, monkeypatch):
    """A project whose single pin declares a patch."""
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"foo": {"type": "github", "url": "https://github.com/o/r",
                     "ref": "main", "patches": [{"url": "https://e/1.diff"}]}},
            {"foo": "/decl.nix"},
            [],
        ),
    )
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "1" * 40)
    monkeypatch.setattr("pnix.prefetch.tarball",
                        lambda url: ("sha256-AAA", 1788914643))
    monkeypatch.setattr("pnix.discover.candidates",
                        lambda roots, attr="pins": [tmp_path / "decl.nix"])
    monkeypatch.setattr(
        "pnix.patches.resolve",
        lambda spec, name, project: [{"kind": "url",
                                      "url": "https://e/1.diff",
                                      "hash": "sha256-PPP"}],
    )
    return tmp_path


def test_update_records_a_patched_hash(patched_project, monkeypatch):
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda project, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-TREE"
                                                      for n in names})
    cli.main(["--project", str(patched_project), "update"])
    doc = lock.read(patched_project / cli.LOCK_NAME)
    assert doc["foo"]["patchedHash"] == "sha256-TREE"


def test_an_unpatched_pin_gets_no_patched_hash(fake_project, monkeypatch):
    called = []
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": called.append(names) or {})
    cli.main(["--project", str(fake_project), "update"])
    doc = lock.read(fake_project / cli.LOCK_NAME)
    assert "patchedHash" not in doc["foo"]
    assert called == []


def test_a_second_update_does_not_rebuild(patched_project, monkeypatch):
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-TREE" for n in names})
    cli.main(["--project", str(patched_project), "update"])
    calls = []
    monkeypatch.setattr(
        "pnix.patchhash.compute",
        lambda p, pins, names, nixpkgs_pin="nixpkgs": calls.append(names) or {n: "sha256-OTHER"
                                                      for n in names},
    )
    cli.main(["--project", str(patched_project), "update"])
    assert calls == []
    assert lock.read(patched_project / cli.LOCK_NAME)["foo"][
        "patchedHash"] == "sha256-TREE"


def test_verify_patches_rebuilds_a_pin_that_did_not_move(patched_project,
                                                         monkeypatch):
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-TREE" for n in names})
    cli.main(["--project", str(patched_project), "update"])
    calls = []
    monkeypatch.setattr(
        "pnix.patchhash.compute",
        lambda p, pins, names, nixpkgs_pin="nixpkgs": calls.append(names) or {n: "sha256-TREE"
                                                      for n in names},
    )
    cli.main(["--project", str(patched_project), "update", "--verify-patches"])
    assert calls == [["foo"]]


def test_verify_patches_reports_content_that_changed(patched_project,
                                                     monkeypatch, capsys):
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-OLD" for n in names})
    cli.main(["--project", str(patched_project), "update"])
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-NEW" for n in names})
    cli.main(["--project", str(patched_project), "update", "--verify-patches"])
    err = capsys.readouterr().err
    assert "foo" in err and "patched tree changed" in err


def test_verify_patches_with_nothing_patched_does_not_build(fake_project,
                                                            monkeypatch):
    calls = []
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": calls.append(names) or {})
    cli.main(["--project", str(fake_project), "update", "--verify-patches"])
    assert calls == []


def test_a_build_failure_is_a_usage_error_not_a_traceback(patched_project,
                                                          monkeypatch, capsys):
    def boom(project, pins, names, nixpkgs_pin="nixpkgs"):
        raise patchhash.PatchHashError("foo: patch does not apply")

    monkeypatch.setattr("pnix.patchhash.compute", boom)
    rc = cli.main(["--project", str(patched_project), "update"])
    assert rc == 2
    assert "does not apply" in capsys.readouterr().err


def test_look_never_builds(patched_project, monkeypatch):
    calls = []
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": calls.append(names) or {})
    cli.main(["--project", str(patched_project), "look"])
    assert calls == []


def test_a_failing_patch_build_still_reports_a_merged_pr(patched_project,
                                                         monkeypatch, capsys):
    """A merged PR is a common reason a patch stops applying, so the advice
    must not be swallowed by the build failure it explains."""
    monkeypatch.setattr(
        "pnix.patches.resolve",
        lambda spec, name, project: [
            {"kind": "pr", "number": 7, "merged": True, "state": "closed",
             "url": "https://x/7.diff", "hash": "sha256-AAA"}
        ],
    )

    def boom(project, pins, names, nixpkgs_pin="nixpkgs"):
        raise patchhash.PatchHashError("foo: patch does not apply")

    monkeypatch.setattr("pnix.patchhash.compute", boom)
    rc = cli.main(["--project", str(patched_project), "update"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "does not apply" in err
    assert "PR #7 is merged upstream" in err


def test_a_rewritten_entry_keeps_its_patched_hash(patched_project, monkeypatch):
    """An entry rebuilt for a reason unrelated to patching -- here an added
    `dir` -- must not lose the recorded hash, and must not pay a rebuild to
    arrive at the same value."""
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-TREE" for n in names})
    cli.main(["--project", str(patched_project), "update"])

    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"foo": {"type": "github", "url": "https://github.com/o/r",
                     "ref": "main", "dir": "sub",
                     "patches": [{"url": "https://e/1.diff"}]}},
            {"foo": "/decl.nix"},
            [],
        ),
    )
    calls = []
    monkeypatch.setattr(
        "pnix.patchhash.compute",
        lambda p, pins, names, nixpkgs_pin="nixpkgs": calls.append(names) or {n: "sha256-OTHER"
                                                      for n in names},
    )
    cli.main(["--project", str(patched_project), "update"])
    entry = lock.read(patched_project / cli.LOCK_NAME)["foo"]
    assert entry["dir"] == "sub"
    assert calls == []
    assert entry["patchedHash"] == "sha256-TREE"


def test_look_does_not_build_a_lock_that_has_no_patched_hash_yet(
        patched_project, monkeypatch):
    """The state a schema-4 lock migrates into: patches recorded, no hash. A
    report must not start a build to fill it in."""
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-TREE" for n in names})
    cli.main(["--project", str(patched_project), "update"])
    p = patched_project / cli.LOCK_NAME
    pins = lock.read(p)
    del pins["foo"]["patchedHash"]
    lock.write(p, pins)

    calls = []
    monkeypatch.setattr(
        "pnix.patchhash.compute",
        lambda pr, pins_, names, nixpkgs_pin="nixpkgs": calls.append(names) or {n: "sha256-X"
                                                        for n in names},
    )
    cli.main(["--project", str(patched_project), "look"])
    assert calls == []


def _moved_head(monkeypatch):
    monkeypatch.setattr(
        "pnix.patches.resolve",
        lambda spec, name, project: [{"kind": "url",
                                      "url": "https://e/1.diff",
                                      "hash": "sha256-MOVED"}],
    )


def test_repatch_re_resolves_a_patch_node_the_fast_path_would_keep(
        patched_project, monkeypatch):
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-ONE" for n in names})
    cli.main(["--project", str(patched_project), "update"])
    _moved_head(monkeypatch)
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-TWO" for n in names})
    cli.main(["--project", str(patched_project), "update", "--repatch"])
    entry = lock.read(patched_project / cli.LOCK_NAME)["foo"]
    assert entry["patches"][0]["hash"] == "sha256-MOVED"
    assert entry["patchedHash"] == "sha256-TWO"


def test_a_bare_update_does_not_adopt_a_moved_patch(patched_project,
                                                    monkeypatch):
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-ONE" for n in names})
    cli.main(["--project", str(patched_project), "update"])
    _moved_head(monkeypatch)
    cli.main(["--project", str(patched_project), "update"])
    entry = lock.read(patched_project / cli.LOCK_NAME)["foo"]
    assert entry["patches"][0]["hash"] == "sha256-PPP"
    assert entry["patchedHash"] == "sha256-ONE"


def test_repatch_can_name_one_pin(patched_project, monkeypatch):
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-ONE" for n in names})
    cli.main(["--project", str(patched_project), "update"])
    _moved_head(monkeypatch)
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-TWO" for n in names})
    cli.main(["--project", str(patched_project), "update", "--repatch", "foo"])
    entry = lock.read(patched_project / cli.LOCK_NAME)["foo"]
    assert entry["patches"][0]["hash"] == "sha256-MOVED"


def test_repatch_of_an_unknown_pin_is_a_usage_error(patched_project, capsys):
    rc = cli.main(["--project", str(patched_project), "update",
                   "--repatch", "nope"])
    assert rc == 2
    assert "not a declared pin" in capsys.readouterr().err


def test_repatch_leaves_a_pin_it_did_not_name_alone(tmp_path, monkeypatch):
    """Naming one pin must not adopt another pin's moved PR head."""
    decl = {
        "foo": {"type": "github", "url": "https://github.com/o/r",
                "ref": "main", "patches": [{"url": "https://e/1.diff"}]},
        "bar": {"type": "github", "url": "https://github.com/o/s",
                "ref": "main", "patches": [{"url": "https://e/2.diff"}]},
    }
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (decl, {"foo": "/d.nix", "bar": "/d.nix"}, []),
    )
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "1" * 40)
    monkeypatch.setattr("pnix.prefetch.tarball",
                        lambda url: ("sha256-AAA", 1788914643))
    monkeypatch.setattr("pnix.discover.candidates",
                        lambda roots, attr="pins": [tmp_path / "d.nix"])
    monkeypatch.setattr(
        "pnix.patches.resolve",
        lambda spec, name, project: [
            {"kind": "url", "url": spec["patches"][0]["url"],
             "hash": f"sha256-{name.upper()}1"}
        ],
    )
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-T1" for n in names})
    cli.main(["--project", str(tmp_path), "update"])

    monkeypatch.setattr(
        "pnix.patches.resolve",
        lambda spec, name, project: [
            {"kind": "url", "url": spec["patches"][0]["url"],
             "hash": f"sha256-{name.upper()}2"}
        ],
    )
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-T2" for n in names})
    cli.main(["--project", str(tmp_path), "update", "--repatch", "foo"])

    pins = lock.read(tmp_path / cli.LOCK_NAME)
    assert pins["foo"]["patches"][0]["hash"] == "sha256-FOO2"
    assert pins["bar"]["patches"][0]["hash"] == "sha256-BAR1"
    assert pins["foo"]["patchedHash"] == "sha256-T2"
    assert pins["bar"]["patchedHash"] == "sha256-T1"


def test_a_changed_patch_set_is_not_blamed_on_nixpkgs(patched_project,
                                                      monkeypatch, capsys):
    """The warning explains a hash that moved with the patch set held still.
    When --repatch has just changed the patch set, a different hash is the
    expected outcome, and saying "the patch set is the same" is false."""
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-ONE" for n in names})
    cli.main(["--project", str(patched_project), "update"])
    _moved_head(monkeypatch)
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-TWO" for n in names})
    capsys.readouterr()
    cli.main(["--project", str(patched_project), "update", "--repatch"])
    assert "patched tree changed" not in capsys.readouterr().err


def test_look_prints_the_repatch_hint(patched_project, capsys, monkeypatch):
    """`drift` builds the hint; this is the wiring that gets the pin's name to
    it, which a helper-level test cannot check."""
    monkeypatch.setattr(
        "pnix.patchhash.compute",
        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: "sha256-ONE"
                                                       for n in names},
    )
    cli.main(["--project", str(patched_project), "update"])
    monkeypatch.setattr(
        "pnix.patches.drift",
        lambda node, name: [
            f"PR #7 has new commits -- `pnix update --repatch {name}` to adopt"
        ],
    )
    cli.main(["--project", str(patched_project), "look"])
    assert "--repatch foo" in capsys.readouterr().out


def test_the_nixpkgs_pin_can_be_renamed_for_the_patch_build(patched_project,
                                                            monkeypatch):
    """`nixpkgsPin` is an eval-time argument, so a renamed pin needs a flag to
    reach the lock-time build; without one `update` is unusable there."""
    seen = {}
    monkeypatch.setattr(
        "pnix.patchhash.compute",
        lambda p, pins, names, nixpkgs_pin="nixpkgs": seen.update(
            pin=nixpkgs_pin) or {n: "sha256-T" for n in names},
    )
    cli.main(["--project", str(patched_project), "update",
              "--nixpkgs-pin", "nixpkgsUnstable"])
    assert seen["pin"] == "nixpkgsUnstable"


def test_repatch_with_no_names_leaves_unpatched_pins_on_the_fast_path(
        tmp_path, monkeypatch):
    """"all patched pins" must mean that: otherwise a bare --repatch
    re-prefetches every pin in the project."""
    decl = {
        "foo": {"type": "github", "url": "https://github.com/o/r",
                "ref": "main", "patches": [{"url": "https://e/1.diff"}]},
        "plain": {"type": "github", "url": "https://github.com/o/s",
                  "ref": "main"},
    }
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (decl, {"foo": "/d.nix", "plain": "/d.nix"},
                                    []),
    )
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "1" * 40)
    monkeypatch.setattr("pnix.discover.candidates",
                        lambda roots, attr="pins": [tmp_path / "d.nix"])
    monkeypatch.setattr(
        "pnix.patches.resolve",
        lambda spec, name, project: [
            {"kind": "url", "url": spec["patches"][0]["url"],
             "hash": "sha256-P"}
        ],
    )
    monkeypatch.setattr("pnix.patchhash.compute",
                        lambda p, pins, names, nixpkgs_pin="nixpkgs": {
                            n: "sha256-T" for n in names})
    monkeypatch.setattr("pnix.prefetch.tarball",
                        lambda url: ("sha256-AAA", 1788914643))
    cli.main(["--project", str(tmp_path), "update"])

    fetched = []
    monkeypatch.setattr(
        "pnix.prefetch.tarball",
        lambda url: (fetched.append(url), ("sha256-AAA", 1788914643))[1],
    )
    cli.main(["--project", str(tmp_path), "update", "--repatch"])
    # The patched pin is re-resolved, so it is re-prefetched too -- the fast path
    # is all-or-nothing. What must not happen is the unpatched pin being dragged
    # along: on a 21-pin config that is minutes and hundreds of megabytes.
    assert not any("/o/s/" in url for url in fetched)
    assert any("/o/r/" in url for url in fetched)


def test_editing_a_local_patch_file_recomputes_the_hash(tmp_path, monkeypatch):
    """The regression this guards: before, the edited patch kept the recorded
    patchedHash, and on the authoring machine the old FOD output already existed,
    so Nix served the pre-edit tree with no error at all."""
    (tmp_path / ".pnix").mkdir()
    patch_file = tmp_path / ".pnix" / "fix.patch"
    patch_file.write_text("one\n")
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"foo": {"type": "github", "url": "https://github.com/o/r",
                     "ref": "main", "patches": [str(patch_file)]}},
            {"foo": "/d.nix"}, [],
        ),
    )
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "1" * 40)
    monkeypatch.setattr("pnix.prefetch.tarball",
                        lambda url: ("sha256-AAA", 1788914643))
    monkeypatch.setattr("pnix.discover.candidates",
                        lambda roots, attr="pins": [tmp_path / "d.nix"])

    seq = iter(["sha256-ONE", "sha256-TWO"])
    seen = []

    def fake_compute(p, pins, names, nixpkgs_pin="nixpkgs"):
        value = next(seq)
        seen.append(names)
        return {n: value for n in names}

    monkeypatch.setattr("pnix.patchhash.compute", fake_compute)
    cli.main(["--project", str(tmp_path), "update"])
    assert lock.read(tmp_path / cli.LOCK_NAME)["foo"]["patchedHash"] == "sha256-ONE"

    patch_file.write_text("two\n")
    cli.main(["--project", str(tmp_path), "update"])
    entry = lock.read(tmp_path / cli.LOCK_NAME)["foo"]
    assert seen == [["foo"], ["foo"]], "the edit did not trigger a recompute"
    assert entry["patchedHash"] == "sha256-TWO"


def test_an_edited_local_patch_is_not_blamed_on_nixpkgs(tmp_path, monkeypatch,
                                                        capsys):
    """The warning explains a hash that moved with the patch set held still.
    An edited local patch file is not that, and saying so sends the user
    hunting nixpkgs."""
    (tmp_path / ".pnix").mkdir()
    patch_file = tmp_path / ".pnix" / "fix.patch"
    patch_file.write_text("one\n")
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"foo": {"type": "github", "url": "https://github.com/o/r",
                     "ref": "main", "patches": [str(patch_file)]}},
            {"foo": "/d.nix"}, [],
        ),
    )
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "1" * 40)
    monkeypatch.setattr("pnix.prefetch.tarball",
                        lambda url: ("sha256-AAA", 1788914643))
    monkeypatch.setattr("pnix.discover.candidates",
                        lambda roots, attr="pins": [tmp_path / "d.nix"])
    seq = iter(["sha256-ONE", "sha256-TWO"])
    monkeypatch.setattr(
        "pnix.patchhash.compute",
        lambda p, pins, names, nixpkgs_pin="nixpkgs": {n: next(seq)
                                                       for n in names},
    )
    cli.main(["--project", str(tmp_path), "update"])
    patch_file.write_text("two\n")
    capsys.readouterr()
    cli.main(["--project", str(tmp_path), "update"])
    assert "nixpkgs applying it" not in capsys.readouterr().err


def test_the_patch_build_is_given_every_pin_not_only_the_recomputed_ones(
        patched_project, monkeypatch):
    """`patchPkgs` comes from the nixpkgs pin, which is not the pin being
    hashed; handing over only the recomputed ones makes it throw."""
    seen = {}
    monkeypatch.setattr(
        "pnix.collect.collect",
        lambda files, attr="pins": (
            {"foo": {"type": "github", "url": "https://github.com/o/r",
                     "ref": "main", "patches": [{"url": "https://e/1.diff"}]},
             "nixpkgs": {"type": "github", "url": "https://github.com/NixOS/nixpkgs",
                         "ref": "nixos-unstable"}},
            {"foo": "/d.nix", "nixpkgs": "/d.nix"}, [],
        ),
    )
    monkeypatch.setattr(
        "pnix.patchhash.compute",
        lambda p, pins, names, nixpkgs_pin="nixpkgs": seen.update(
            pins=set(pins), names=list(names)) or {n: "sha256-T" for n in names},
    )
    cli.main(["--project", str(patched_project), "update"])
    assert seen["names"] == ["foo"], seen
    assert seen["pins"] == {"foo", "nixpkgs"}, seen


# --- gc roots for patched trees --------------------------------------------

def _root_calls(monkeypatch):
    """Record which store paths got rooted, through the real code path."""
    rooted = {}
    monkeypatch.setattr(
        "pnix.patchhash.root",
        lambda project, built: rooted.update(built),
    )
    return rooted


def test_update_roots_the_resolvers_path_not_the_build_output(
        patched_project, monkeypatch):
    """The whole point of resolving through `paths()`: `compute` returns the
    input-addressed build, which is a different store path from the one the
    recorded hash names."""
    rooted = _root_calls(monkeypatch)
    monkeypatch.setattr(
        "pnix.patchhash.compute",
        lambda project, pins, names, nixpkgs_pin="nixpkgs": {
            n: "sha256-TREE" for n in names
        },
    )
    monkeypatch.setattr(
        "pnix.patchhash.paths",
        lambda project, pins, names, nixpkgs_pin="nixpkgs": {
            n: f"/nix/store/fod-{n}" for n in names
        },
    )
    assert cli.main(["--project", str(patched_project), "update"]) == 0
    assert rooted == {"foo": "/nix/store/fod-foo"}


def test_the_path_lookup_sees_the_hash_that_was_just_recorded(
        patched_project, monkeypatch):
    """`paths()` derives the fixed-output path from the hash, so it has to run
    after the hash lands in `result` -- not before, where the pin would still
    resolve to its input-addressed path."""
    seen = {}
    monkeypatch.setattr(
        "pnix.patchhash.compute",
        lambda project, pins, names, nixpkgs_pin="nixpkgs": {
            n: "sha256-FRESH" for n in names
        },
    )

    def fake_paths(project, pins, names, nixpkgs_pin="nixpkgs"):
        seen.update({n: pins[n].get("patchedHash") for n in names})
        return {}

    monkeypatch.setattr("pnix.patchhash.paths", fake_paths)
    cli.main(["--project", str(patched_project), "update"])
    assert seen == {"foo": "sha256-FRESH"}


def test_a_pin_with_no_patches_is_unrooted(fake_project, monkeypatch):
    """Otherwise a pin that loses its patches keeps its last patched tree alive
    forever -- the one way this could leak store space instead of saving
    rebuild time."""
    pruned = {}
    monkeypatch.setattr("pnix.patchhash.prune",
                        lambda project, keep: pruned.update(keep=keep))
    monkeypatch.setattr("pnix.patchhash.paths", lambda *a, **k: {})
    assert cli.main(["--project", str(fake_project), "update"]) == 0
    assert pruned.get("keep") == set()
