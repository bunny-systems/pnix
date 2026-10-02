import json
import re
import subprocess
from pathlib import Path

import pytest

from pnix import patchhash


def test_recompute_when_there_is_no_hash_yet():
    assert patchhash.needs_recompute({"patches": [1], "rev": "a"}, {})


def test_no_recompute_when_nothing_about_the_patches_moved():
    entry = {"patches": [{"hash": "H"}], "rev": "a", "patchedHash": "sha256-A"}
    assert not patchhash.needs_recompute(entry, entry)


def test_recompute_when_a_patch_node_moved():
    prior = {"patches": [{"hash": "H"}], "rev": "a", "patchedHash": "sha256-A"}
    now = {"patches": [{"hash": "J"}], "rev": "a", "patchedHash": "sha256-A"}
    assert patchhash.needs_recompute(now, prior)


def test_recompute_when_the_source_rev_moved():
    prior = {"patches": [{"hash": "H"}], "rev": "a", "patchedHash": "sha256-A"}
    now = {"patches": [{"hash": "H"}], "rev": "b", "patchedHash": "sha256-A"}
    assert patchhash.needs_recompute(now, prior)


def test_verify_all_recomputes_even_when_nothing_moved():
    entry = {"patches": [{"hash": "H"}], "rev": "a", "patchedHash": "sha256-A"}
    assert patchhash.needs_recompute(entry, entry, verify_all=True)


def test_an_unpatched_pin_is_never_recomputed():
    assert not patchhash.needs_recompute({"rev": "a"}, {}, verify_all=True)


def _capture(monkeypatch, paths=2):
    """Record every subprocess argv and answer it plausibly."""
    calls = []

    def fake_run(argv, *a, **kw):
        calls.append(argv)
        if argv[0] == "nix-build":
            out = "\n".join(f"/nix/store/fake-{i}" for i in range(paths))
            return subprocess.CompletedProcess(argv, 0, out + "\n", "")
        if argv[0] == "nix-hash":
            # Derived from the path, so a mis-ordered zip is observable. A
            # constant here made the ordering test vacuous.
            target = next(a for a in argv if a.startswith("/nix/store"))
            return subprocess.CompletedProcess(
                argv, 0, f"sha256-{target.rsplit('-', 1)[-1]}\n", "")
        raise AssertionError(f"unexpected binary: {argv}")

    monkeypatch.setattr("subprocess.run", fake_run)
    return calls


def test_the_temp_lock_sits_beside_the_real_one(tmp_path, monkeypatch):
    """A `path` patch resolves against dirOf lockFile, so a temp lock in /tmp
    cannot find it."""
    calls = _capture(monkeypatch, paths=1)
    (tmp_path / ".pnix").mkdir()
    pins = {"a": {"patches": [{"kind": "path", "path": "p.patch"}]}}
    patchhash.compute(tmp_path, pins, ["a"])
    built = next(c for c in calls if c[0] == "nix-build")
    expr = built[built.index("-E") + 1]
    # Emitted as a Nix path via `/. + "rel"`, so the leading slash is gone.
    assert str(tmp_path / ".pnix").lstrip("/") in expr


def test_a_stale_hash_is_stripped_before_building(tmp_path, monkeypatch):
    """Leaving it in makes the build validate against the very value being
    recomputed, so it fails instead of answering."""
    seen = {}

    def fake_run(argv, *a, **kw):
        if argv[0] == "nix-build":
            expr = argv[argv.index("-E") + 1]
            rel = re.search(r'lockFile = \(/\. \+ "([^"]+)"\)', expr).group(1)
            lock_path = Path("/") / rel
            seen["doc"] = json.loads(lock_path.read_text())
            return subprocess.CompletedProcess(argv, 0, "/nix/store/fake-0\n", "")
        return subprocess.CompletedProcess(argv, 0, "sha256-HASH\n", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    (tmp_path / ".pnix").mkdir()
    pins = {"a": {"patches": [{"hash": "H"}], "patchedHash": "sha256-STALE"}}
    patchhash.compute(tmp_path, pins, ["a"])
    assert "patchedHash" not in seen["doc"]["pins"]["a"]


def test_the_temp_lock_is_removed_afterwards(tmp_path, monkeypatch):
    _capture(monkeypatch, paths=1)
    (tmp_path / ".pnix").mkdir()
    patchhash.compute(tmp_path, {"a": {"patches": [{"hash": "H"}]}}, ["a"])
    assert list((tmp_path / ".pnix").glob("*pnix-tmp*")) == []


def test_no_experimental_features_are_enabled(tmp_path, monkeypatch):
    calls = _capture(monkeypatch, paths=1)
    (tmp_path / ".pnix").mkdir()
    patchhash.compute(tmp_path, {"a": {"patches": [{"hash": "H"}]}}, ["a"])
    assert calls
    for call in calls:
        assert "experimental-features" in call
        assert call[call.index("experimental-features") + 1] == ""


def test_a_failed_build_names_the_pin(tmp_path, monkeypatch):
    def fake_run(argv, *a, **kw):
        return subprocess.CompletedProcess(argv, 1, "", "patch does not apply")

    monkeypatch.setattr("subprocess.run", fake_run)
    (tmp_path / ".pnix").mkdir()
    with pytest.raises(patchhash.PatchHashError) as e:
        patchhash.compute(tmp_path, {"finit": {"patches": [{"hash": "H"}]}},
                          ["finit"])
    assert "finit" in str(e.value)
    assert "does not apply" in str(e.value)


def test_a_path_count_mismatch_refuses_to_guess(tmp_path, monkeypatch):
    _capture(monkeypatch, paths=1)
    (tmp_path / ".pnix").mkdir()
    pins = {"a": {"patches": [{"hash": "H"}]}, "b": {"patches": [{"hash": "H"}]}}
    with pytest.raises(patchhash.PatchHashError) as e:
        patchhash.compute(tmp_path, pins, ["a", "b"])
    assert "refusing to guess" in str(e.value)


def test_names_map_to_hashes_in_the_order_they_were_asked_for(tmp_path,
                                                              monkeypatch):
    _capture(monkeypatch, paths=2)
    (tmp_path / ".pnix").mkdir()
    pins = {"b": {"patches": [{"hash": "H"}]},
            "a": {"patches": [{"hash": "H"}]}}
    out = patchhash.compute(tmp_path, pins, ["b", "a"])
    # nix-build prints one path per line in the order the expression asked for,
    # so the first path belongs to "b". A reversed zip gives each pin the other's
    # hash, which ships as a mismatch on every consumer.
    assert out == {"b": "sha256-0", "a": "sha256-1"}


def test_nothing_to_do_runs_no_command(tmp_path, monkeypatch):
    calls = _capture(monkeypatch)
    assert patchhash.compute(tmp_path, {}, []) == {}
    assert calls == []


def test_the_lock_is_passed_as_a_nix_path_not_a_string(tmp_path, monkeypatch):
    """A local patch is `dirOf lockFile + path`. If lockFile is a Nix string,
    that stays a string, so nothing copies the patch into the store and the
    sandboxed builder cannot read it. A path value is copied."""
    calls = _capture(monkeypatch, paths=1)
    (tmp_path / ".pnix").mkdir()
    patchhash.compute(tmp_path, {"a": {"patches": [{"hash": "H"}]}}, ["a"])
    built = next(c for c in calls if c[0] == "nix-build")
    expr = built[built.index("-E") + 1]
    assert 'lockFile = "' not in expr
    assert 'lockFile = (/. + "' in expr


def test_hashing_uses_the_stable_cli(tmp_path, monkeypatch):
    """`nix hash path` needs the nix-command experimental feature, which
    NO_EXPERIMENTAL switches off -- so it fails on every real invocation while
    every stubbed one passes."""
    calls = _capture(monkeypatch, paths=1)
    (tmp_path / ".pnix").mkdir()
    patchhash.compute(tmp_path, {"a": {"patches": [{"hash": "H"}]}}, ["a"])
    hashing = [c for c in calls if c[0] != "nix-build"]
    assert hashing, "nothing hashed the built tree"
    for call in hashing:
        assert call[0] == "nix-hash"


def _expr_of(calls):
    built = next(c for c in calls if c[0] == "nix-build")
    return built[built.index("-E") + 1]


def test_the_environment_cannot_reach_the_lock_time_build(tmp_path, monkeypatch):
    """PNIX_OVERRIDE would otherwise replace the tree the hash is taken from,
    writing an override's hash into a committed lock that no later `pnix update`
    repairs."""
    calls = _capture(monkeypatch, paths=1)
    (tmp_path / ".pnix").mkdir()
    patchhash.compute(tmp_path, {"a": {"patches": [{"hash": "H"}]}}, ["a"])
    assert "overrideVar = null;" in _expr_of(calls)


def test_the_nixpkgs_pin_can_be_named(tmp_path, monkeypatch):
    """A project that renamed its nixpkgs pin cannot pass `nixpkgsPin` here --
    that is an eval-time argument -- so `update` has to be told."""
    calls = _capture(monkeypatch, paths=1)
    (tmp_path / ".pnix").mkdir()
    patchhash.compute(tmp_path, {"a": {"patches": [{"hash": "H"}]}}, ["a"],
                      nixpkgs_pin="nixpkgsUnstable")
    assert 'nixpkgsPin = "nixpkgsUnstable";' in _expr_of(calls)


def test_the_build_asks_for_the_patched_derivations(tmp_path, monkeypatch):
    """Without patchedOnly the expression yields sourceInfo attrsets, which
    nix-build cannot realise."""
    calls = _capture(monkeypatch, paths=1)
    (tmp_path / ".pnix").mkdir()
    patchhash.compute(tmp_path, {"a": {"patches": [{"hash": "H"}]}}, ["a"])
    assert "patchedOnly = true;" in _expr_of(calls)


def test_the_build_uses_pnix_s_own_resolver(tmp_path, monkeypatch):
    """Not the project's vendored .pnix/eval, which may predate this pnix."""
    calls = _capture(monkeypatch, paths=1)
    (tmp_path / ".pnix").mkdir()
    patchhash.compute(tmp_path, {"a": {"patches": [{"hash": "H"}]}}, ["a"])
    expr = _expr_of(calls)
    # Spelled literally rather than via patchhash.RESOLVER, which would move
    # with the bug and assert nothing.
    own = Path(patchhash.__file__).resolve().parent
    assert f"{str(own).lstrip('/')}/resolver/eval/resolve.nix" in expr
    assert str(tmp_path / ".pnix").lstrip("/") + "/eval" not in expr


def test_the_tree_is_hashed_recursively_not_flat(tmp_path, monkeypatch):
    """A patched pin is a directory; --flat would hash the wrong thing and the
    recorded value would never match what Nix produces."""
    calls = _capture(monkeypatch, paths=1)
    (tmp_path / ".pnix").mkdir()
    patchhash.compute(tmp_path, {"a": {"patches": [{"hash": "H"}]}}, ["a"])
    hashing = next(c for c in calls if c[0] == "nix-hash")
    assert "--flat" not in hashing


def test_the_temp_lock_carries_every_pin_not_only_the_named_ones(tmp_path,
                                                                monkeypatch):
    """`patchPkgs` comes from the nixpkgs pin, which is rarely the pin being
    hashed."""
    seen = {}

    def fake_run(argv, *a, **kw):
        if argv[0] == "nix-build":
            expr = argv[argv.index("-E") + 1]
            rel = re.search(r'lockFile = \(/\. \+ "([^"]+)"\)', expr).group(1)
            seen["doc"] = json.loads((Path("/") / rel).read_text())
            return subprocess.CompletedProcess(argv, 0, "/nix/store/f-0\n", "")
        return subprocess.CompletedProcess(argv, 0, "sha256-HASH\n", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    (tmp_path / ".pnix").mkdir()
    pins = {"a": {"patches": [{"hash": "H"}]}, "nixpkgs": {"rev": "b" * 40}}
    patchhash.compute(tmp_path, pins, ["a"])
    assert set(seen["doc"]["pins"]) == {"a", "nixpkgs"}


FAILING_LOG = "\n".join(
    [f"filler line {i}" for i in range(40)]
    + ["building '/nix/store/aaaa-b-patched.drv'...",
       "Running phase: patchPhase",
       "applying patch /nix/store/bbbb-fix.patch",
       "1 out of 2 hunks FAILED -- saving rejects",
       "error: builder for '/nix/store/aaaa-b-patched.drv' failed"]
)


def test_a_failure_names_the_pin_that_failed_not_every_pin_recomputed(
        tmp_path, monkeypatch):
    def fake_run(argv, *a, **kw):
        if argv[0] == "nix-build":
            return subprocess.CompletedProcess(argv, 1, "", FAILING_LOG)
        return subprocess.CompletedProcess(argv, 0, "sha256-HASH\n", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    (tmp_path / ".pnix").mkdir()
    pins = {n: {"patches": [{"hash": "H"}]} for n in ("a", "b", "c")}
    with pytest.raises(patchhash.PatchHashError) as e:
        patchhash.compute(tmp_path, pins, ["a", "b", "c"])
    msg = str(e.value)
    assert msg.startswith("b:"), msg
    assert "a, b, c" not in msg


def test_a_failure_says_how_to_make_progress(tmp_path, monkeypatch):
    def fake_run(argv, *a, **kw):
        if argv[0] == "nix-build":
            return subprocess.CompletedProcess(argv, 1, "", FAILING_LOG)
        return subprocess.CompletedProcess(argv, 0, "sha256-HASH\n", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    (tmp_path / ".pnix").mkdir()
    with pytest.raises(patchhash.PatchHashError) as e:
        patchhash.compute(tmp_path, {"b": {"patches": [{"hash": "H"}]}}, ["b"])
    assert "--exclude b" in str(e.value)


def test_a_failure_shows_the_tail_of_the_log_not_all_of_it(tmp_path,
                                                           monkeypatch):
    def fake_run(argv, *a, **kw):
        if argv[0] == "nix-build":
            return subprocess.CompletedProcess(argv, 1, "", FAILING_LOG)
        return subprocess.CompletedProcess(argv, 0, "sha256-HASH\n", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    (tmp_path / ".pnix").mkdir()
    with pytest.raises(patchhash.PatchHashError) as e:
        patchhash.compute(tmp_path, {"b": {"patches": [{"hash": "H"}]}}, ["b"])
    msg = str(e.value)
    assert "hunks FAILED" in msg
    assert "filler line 0" not in msg


def test_a_failure_pnix_cannot_attribute_still_names_the_candidates(
        tmp_path, monkeypatch):
    def fake_run(argv, *a, **kw):
        if argv[0] == "nix-build":
            return subprocess.CompletedProcess(argv, 1, "", "error: out of disk")
        return subprocess.CompletedProcess(argv, 0, "sha256-HASH\n", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    (tmp_path / ".pnix").mkdir()
    pins = {n: {"patches": [{"hash": "H"}]} for n in ("a", "b")}
    with pytest.raises(patchhash.PatchHashError) as e:
        patchhash.compute(tmp_path, pins, ["a", "b"])
    assert "a, b" in str(e.value)


def test_a_missing_nixpkgs_pin_is_advised_with_the_right_flag(tmp_path,
                                                             monkeypatch):
    """"Fix the patch" is the wrong remedy when the patch was never reached."""
    log = ("error: pnix: a pin declares patches, which need a nixpkgs to apply "
           "them, but there is no pin called 'nixpkgs'. Pass `nixpkgsPin` to "
           "name it.")

    def fake_run(argv, *a, **kw):
        if argv[0] == "nix-build":
            return subprocess.CompletedProcess(argv, 1, "", log)
        return subprocess.CompletedProcess(argv, 0, "sha256-HASH\n", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    (tmp_path / ".pnix").mkdir()
    with pytest.raises(patchhash.PatchHashError) as e:
        patchhash.compute(tmp_path, {"b": {"patches": [{"hash": "H"}]}}, ["b"])
    msg = str(e.value)
    assert "--nixpkgs-pin" in msg
    assert "Fix the patch" not in msg


# --- garbage-collection roots ----------------------------------------------
#
# Every test here uses real directories as stand-in store paths, because
# `root` refuses a path that is not on disk -- that guard is what stops
# `nix-store --realise` turning an update into a 46 s build. A test using
# `/nix/store/fake-0` would exercise nothing, which is exactly what the
# pre-existing `_capture` tests were doing by accident.

def _roots(monkeypatch, tmp_path, fail=False):
    """Record nix-store invocations; the state dir lands under tmp_path."""
    calls = []

    def fake_run(argv, *a, **kw):
        calls.append(argv)
        if argv[0] == "nix-store":
            return subprocess.CompletedProcess(
                argv, 1 if fail else 0, "", "nix-store: nope" if fail else "")
        raise AssertionError(f"unexpected binary: {argv}")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return calls


def test_the_state_dir_is_under_xdg_state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    d = patchhash._state_dir(tmp_path / "proj")
    assert str(d).startswith(str(tmp_path / "s" / "pnix"))
    assert d.name == patchhash.ROOTS


def test_two_checkouts_do_not_share_roots(tmp_path, monkeypatch):
    """Two clones of one config are two projects; sharing links would let one
    clone's update unroot the other's tree."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    assert (patchhash._state_dir(tmp_path / "a")
            != patchhash._state_dir(tmp_path / "b"))


def test_the_root_is_outside_the_project(tmp_path, monkeypatch):
    """A symlink into /nix/store inside the repo is something `git add -A`
    picks up, and it would outlive the project."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    proj = tmp_path / "proj"
    assert proj not in patchhash._state_dir(proj).parents


def test_rooting_calls_add_root_for_a_path_that_exists(tmp_path, monkeypatch):
    calls = _roots(monkeypatch, tmp_path)
    tree = tmp_path / "tree"
    tree.mkdir()
    patchhash.root(tmp_path / "proj", {"foo": str(tree)})
    assert len(calls) == 1
    argv = calls[0]
    assert argv[:3] == ["nix-store", "--realise", str(tree)]
    link = argv[argv.index("--add-root") + 1]
    assert Path(link).name == "foo"
    assert Path(link).parent.name == patchhash.ROOTS


def test_rooting_never_realises_a_path_that_is_gone(tmp_path, monkeypatch):
    """`--realise` on a missing path builds it. An update that silently started
    a nixpkgs-sized build would be worse than a lost root."""
    calls = _roots(monkeypatch, tmp_path)
    patchhash.root(tmp_path / "proj", {"foo": "/nix/store/definitely-not-here"})
    assert calls == []


def test_a_failed_root_warns_and_does_not_raise(tmp_path, monkeypatch, capsys):
    """A missing root costs a rebuild; aborting the run costs the whole
    update."""
    _roots(monkeypatch, tmp_path, fail=True)
    tree = tmp_path / "tree"
    tree.mkdir()
    patchhash.root(tmp_path / "proj", {"foo": str(tree)})
    err = capsys.readouterr().err
    assert "foo" in err and "garbage collection" in err


def test_pruning_drops_only_the_pins_that_are_no_longer_patched(tmp_path,
                                                                monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    proj = tmp_path / "proj"
    roots = patchhash._state_dir(proj)
    roots.mkdir(parents=True)
    (roots / "keep").symlink_to(tmp_path)
    (roots / "drop").symlink_to(tmp_path)
    patchhash.prune(proj, {"keep"})
    assert [p.name for p in sorted(roots.iterdir())] == ["keep"]


def test_pruning_a_project_with_no_roots_is_quiet(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    patchhash.prune(tmp_path / "never-updated", {"foo"})


def test_computing_does_not_root_its_own_build(tmp_path, monkeypatch):
    """`compute` builds from a lock with the hashes *stripped*, so it takes the
    input-addressed branch and its output is not the path the resolver will
    name. Measured on a real project: `ch7d91z...-demo-patched` from here
    against `yd1qisr...-demo-patched` from the fixed-output branch -- same
    tree, same hash, different path. Rooting this one would protect a tree
    nothing ever asks for again."""
    tree = tmp_path / "built"
    tree.mkdir()
    calls = []

    def fake_run(argv, *a, **kw):
        calls.append(argv)
        if argv[0] == "nix-build":
            return subprocess.CompletedProcess(argv, 0, f"{tree}\n", "")
        if argv[0] == "nix-hash":
            return subprocess.CompletedProcess(argv, 0, "sha256-BUILT\n", "")
        if argv[0] == "nix-store":
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected binary: {argv}")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    proj = tmp_path / "proj"
    (proj / ".pnix").mkdir(parents=True)
    out = patchhash.compute(proj, {"foo": {"patches": [1]}}, ["foo"])
    assert out == {"foo": "sha256-BUILT"}
    # It does call nix-store, to place the tree at its locked path; what it must
    # not do is root this path.
    assert not any("--add-root" in a for a in calls)


def test_paths_reads_the_store_paths_out_of_an_evaluation(tmp_path,
                                                          monkeypatch):
    """Evaluation, not a build: a recorded hash is what makes a fixed-output
    path knowable up front."""
    def fake_run(argv, *a, **kw):
        assert argv[0] == "nix-instantiate", argv
        assert "--eval" in argv
        assert "nix-build" not in argv
        return subprocess.CompletedProcess(
            argv, 0, json.dumps(["/nix/store/a-one", "/nix/store/b-two"]), "")

    monkeypatch.setattr("subprocess.run", fake_run)
    proj = tmp_path / "proj"
    (proj / ".pnix").mkdir(parents=True)
    got = patchhash.paths(proj, {"one": {}, "two": {}}, ["one", "two"])
    assert got == {"one": "/nix/store/a-one", "two": "/nix/store/b-two"}


def test_paths_keeps_the_recorded_hashes_in_the_temp_lock(tmp_path,
                                                          monkeypatch):
    """Stripping them here would drop the pin to the input-addressed branch,
    where the path depends on the nixpkgs applying the patch -- so the root
    would protect a tree nothing will ask for."""
    seen = {}
    proj = tmp_path / "proj"
    (proj / ".pnix").mkdir(parents=True)

    def fake_run(argv, *a, **kw):
        # Read it off disk while the call is in flight: the expression is one
        # argv element, so picking the path back out of it is its own bug.
        assert any(patchhash.TMP_NAME in x for x in argv), argv
        seen["lock"] = json.loads(
            (proj / ".pnix" / patchhash.TMP_NAME).read_text())
        return subprocess.CompletedProcess(argv, 0, json.dumps(["/nix/store/x"]),
                                           "")

    monkeypatch.setattr("subprocess.run", fake_run)
    patchhash.paths(proj, {"one": {"patchedHash": "sha256-KEEP"}}, ["one"])
    assert seen["lock"]["pins"]["one"]["patchedHash"] == "sha256-KEEP"


def test_a_failed_path_lookup_is_not_fatal(tmp_path, monkeypatch):
    def fake_run(argv, *a, **kw):
        return subprocess.CompletedProcess(argv, 1, "", "no pin called nixpkgs")

    monkeypatch.setattr("subprocess.run", fake_run)
    proj = tmp_path / "proj"
    (proj / ".pnix").mkdir(parents=True)
    assert patchhash.paths(proj, {"one": {}}, ["one"]) == {}


def test_the_temp_lock_is_removed_after_a_path_lookup(tmp_path, monkeypatch):
    def fake_run(argv, *a, **kw):
        return subprocess.CompletedProcess(argv, 0, json.dumps(["/x"]), "")

    monkeypatch.setattr("subprocess.run", fake_run)
    proj = tmp_path / "proj"
    (proj / ".pnix").mkdir(parents=True)
    patchhash.paths(proj, {"one": {}}, ["one"])
    assert not (proj / ".pnix" / patchhash.TMP_NAME).exists()


# --- placing the tree at the path the lock names ---------------------------

def _materialising(monkeypatch, added=None, rc=0):
    """Stub nix-store's `--add-fixed`, recording the directory it was given."""
    calls = []

    def fake_run(argv, *a, **kw):
        calls.append(argv)
        if argv[0] == "nix-store":
            target = argv[argv.index("sha256") + 1]
            out = added if added is not None else (
                f"/nix/store/deadbeef-{Path(target).name}")
            return subprocess.CompletedProcess(argv, rc, f"{out}\n", "boom")
        raise AssertionError(f"unexpected binary: {argv}")

    monkeypatch.setattr("subprocess.run", fake_run)
    return calls


def test_the_tree_is_staged_under_the_name_the_resolver_asks_for(tmp_path,
                                                                 monkeypatch):
    """`--add-fixed` takes the name from the basename and has no `--name`, and a
    store path's basename carries a hash prefix -- so a copy named exactly
    `<pin>-patched` is the whole reason this step exists."""
    calls = _materialising(monkeypatch)
    tree = tmp_path / "abc123-foo-patched"
    tree.mkdir()
    (tree / "f").write_text("x")
    got = patchhash._materialise("foo", str(tree), "sha256-AAA")
    assert got == "/nix/store/deadbeef-foo-patched"
    argv = calls[0]
    assert argv[:4] == ["nix-store", "--add-fixed", "--recursive", "sha256"]
    assert Path(argv[4]).name == "foo-patched"


def test_the_staged_copy_is_removed_even_though_store_trees_are_read_only(
        tmp_path, monkeypatch):
    """A mode-555 directory cannot have its contents unlinked, so without
    putting the write bit back the staging copy outlives the run -- and for a
    nixpkgs-sized tree that is 333 MB left in TMPDIR."""
    seen = {}

    def fake_run(argv, *a, **kw):
        seen["staged"] = argv[argv.index("sha256") + 1]
        return subprocess.CompletedProcess(
            argv, 0, "/nix/store/deadbeef-foo-patched\n", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    tree = tmp_path / "src"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "f").write_text("x")
    (tree / "sub" / "f").chmod(0o444)
    (tree / "sub").chmod(0o555)
    tree.chmod(0o555)
    patchhash._materialise("foo", str(tree), "sha256-AAA")
    assert not Path(seen["staged"]).parent.exists()


def test_the_suffix_matches_what_patch_nix_names_the_derivation():
    """If `patch.nix` is renamed and this is not, every `--add-fixed` lands on a
    path nothing asks for and the tree is quietly built twice again."""
    patch_nix = (Path(__file__).resolve().parent.parent
                 / "pnix" / "resolver" / "eval" / "patch.nix")
    assert f'name = "${{name}}{patchhash.SUFFIX}"' in patch_nix.read_text()


def test_a_wrongly_named_result_is_reported_rather_than_left_in_the_store(
        tmp_path, monkeypatch, capsys):
    _materialising(monkeypatch, added="/nix/store/deadbeef-something-else")
    tree = tmp_path / "t"
    tree.mkdir()
    assert patchhash._materialise("foo", str(tree), "sha256-AAA") is None
    assert "not the name the resolver asks for" in capsys.readouterr().err


def test_a_failed_add_is_not_fatal(tmp_path, monkeypatch, capsys):
    """The lock is already correct; the only consequence is the rebuild that
    used to happen unconditionally."""
    _materialising(monkeypatch, rc=1)
    tree = tmp_path / "t"
    tree.mkdir()
    assert patchhash._materialise("foo", str(tree), "sha256-AAA") is None
    assert "rebuilt on first use" in capsys.readouterr().err


def test_a_tree_that_cannot_be_staged_is_not_fatal(tmp_path, monkeypatch,
                                                   capsys):
    def fake_run(argv, *a, **kw):
        raise AssertionError("nix-store must not run when staging failed")

    monkeypatch.setattr("subprocess.run", fake_run)
    assert patchhash._materialise("foo", str(tmp_path / "gone"),
                                  "sha256-AAA") is None
    assert "could not stage" in capsys.readouterr().err


def test_only_a_sha256_hash_is_placed(tmp_path, monkeypatch):
    """`--add-fixed sha256` is the only form whose path matches a recursive
    sha256 fixed-output derivation; anything else would land elsewhere."""
    def fake_run(argv, *a, **kw):
        raise AssertionError("must not run for a non-sha256 hash")

    monkeypatch.setattr("subprocess.run", fake_run)
    assert patchhash._materialise("foo", str(tmp_path), "sha512-AAA") is None


def test_computing_places_every_tree_it_built(tmp_path, monkeypatch):
    """The step that turns two builds per patch change into one. Without this
    assertion, dropping the call from `compute` passes the whole suite."""
    calls = []
    trees = {}
    for name in ("one", "two"):
        t = tmp_path / f"hash-{name}-patched"
        t.mkdir()
        (t / "f").write_text(name)
        trees[name] = t

    def fake_run(argv, *a, **kw):
        calls.append(argv)
        if argv[0] == "nix-build":
            return subprocess.CompletedProcess(
                argv, 0, "\n".join(str(trees[n]) for n in ("one", "two")) + "\n",
                "")
        if argv[0] == "nix-hash":
            return subprocess.CompletedProcess(argv, 0, "sha256-AAA\n", "")
        if argv[0] == "nix-store":
            target = argv[argv.index("sha256") + 1]
            return subprocess.CompletedProcess(
                argv, 0, f"/nix/store/deadbeef-{Path(target).name}\n", "")
        raise AssertionError(f"unexpected binary: {argv}")

    monkeypatch.setattr("subprocess.run", fake_run)
    proj = tmp_path / "proj"
    (proj / ".pnix").mkdir(parents=True)
    patchhash.compute(proj, {"one": {"patches": [1]}, "two": {"patches": [1]}},
                      ["one", "two"])
    staged = [Path(a[a.index("sha256") + 1]).name
              for a in calls if "--add-fixed" in a]
    assert staged == ["one-patched", "two-patched"]


def test_a_relative_project_path_still_finds_the_lock(tmp_path, monkeypatch):
    """`_nix_path` strips the leading slash, so a relative project path used to
    produce `/.pnix/...` and fail on a path that plainly exists. Found by
    tripping over it while building a real project by hand."""
    proj = tmp_path / "proj"
    (proj / ".pnix").mkdir(parents=True)
    seen = {}

    def fake_run(argv, *a, **kw):
        if argv[0] == "nix-build":
            seen["expr"] = next(x for x in argv if patchhash.TMP_NAME in x)
            return subprocess.CompletedProcess(argv, 0, "/nix/store/x-foo\n", "")
        if argv[0] == "nix-hash":
            return subprocess.CompletedProcess(argv, 0, "sha256-A\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.chdir(tmp_path)
    patchhash.compute(Path("proj"), {"foo": {"patches": [1]}}, ["foo"])
    # An unresolved relative path yields `(/. + ".pnix/...")`, which is the root
    # of the filesystem rather than the project.
    assert '"proj/.pnix/' not in seen["expr"]
    assert str(proj).lstrip("/") in seen["expr"]
