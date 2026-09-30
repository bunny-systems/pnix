import pytest

from pnix import refs


def test_resolves_branch(local_repo, local_repo_head):
    assert refs.resolve(f"file://{local_repo}", "main") == local_repo_head


def test_resolves_lightweight_tag(local_repo, local_repo_head):
    assert refs.resolve(f"file://{local_repo}", "v1.0.0") == local_repo_head


def test_resolves_annotated_tag_to_the_commit(local_repo, local_repo_head):
    """refs/tags/v2.0.0 is the tag object; only ^{} is the commit.

    Writing the tag-object sha into the lock is silent: it is 40 well-formed
    hex characters, and nothing notices until a fetcher cannot get a tree
    from it.
    """
    assert refs.resolve(f"file://{local_repo}", "v2.0.0") == local_repo_head


def test_resolves_default_head_when_ref_is_none(local_repo, local_repo_head):
    assert refs.resolve(f"file://{local_repo}") == local_repo_head


def test_resolves_a_fully_qualified_ref(local_repo, local_repo_head):
    assert refs.resolve(f"file://{local_repo}",
                        "refs/heads/main") == local_repo_head


def test_unknown_ref_raises(local_repo):
    with pytest.raises(refs.RefError) as e:
        refs.resolve(f"file://{local_repo}", "no-such-branch")
    assert "no-such-branch" in str(e.value)


def test_resolve_many_keeps_the_mapping(local_repo, local_repo_head):
    url = f"file://{local_repo}"
    out = refs.resolve_many({
        "a": (url, "main"),
        "b": (url, "v1.0.0"),
        "c": (url, None),
        "d": (url, "v2.0.0"),
    })
    assert out == {k: local_repo_head for k in ("a", "b", "c", "d")}


def test_resolve_many_reports_every_failure_not_just_the_first(local_repo):
    url = f"file://{local_repo}"
    with pytest.raises(refs.RefError) as e:
        refs.resolve_many({
            "good": (url, "main"),
            "bad1": (url, "nope-one"),
            "bad2": (url, "nope-two"),
        })
    msg = str(e.value)
    assert "bad1" in msg and "bad2" in msg


def test_resolve_many_is_actually_concurrent(monkeypatch):
    """Guards the whole point of the function. Without a pool this is ~1.6 s."""
    import time

    def slow(url, ref=None):
        time.sleep(0.2)
        return "0" * 40

    monkeypatch.setattr("pnix.refs.resolve", slow)
    targets = {f"p{i}": ("file:///nowhere", None) for i in range(8)}

    start = time.monotonic()
    out = refs.resolve_many(targets, workers=8)
    elapsed = time.monotonic() - start

    assert len(out) == 8
    assert elapsed < 0.8, f"resolve_many looks serial ({elapsed:.2f}s for 8x0.2s)"


def test_resolve_many_handles_an_empty_set():
    assert refs.resolve_many({}) == {}


# --- tag and release strategies -------------------------------------------

def test_tags_reports_commits_not_tag_objects(local_repo, local_repo_head):
    """`ls-remote --tags` lists an annotated tag twice; the peeled row is the
    commit. Comparing tag-object shas yields a rev no fetcher can use."""
    found = refs.tags(f"file://{local_repo}")
    assert found == {"v1.0.0": local_repo_head, "v2.0.0": local_repo_head}


def test_resolve_tag_refuses_to_fall_back_to_a_branch(local_repo):
    """`resolve` follows gitrevisions and would answer with refs/heads/main.
    A declaration that says `tag` means a tag."""
    with pytest.raises(refs.RefError) as e:
        refs.resolve_tag(f"file://{local_repo}", "main")
    assert "no tag" in str(e.value)


def test_resolve_tag_peels_an_annotated_tag(local_repo, local_repo_head):
    assert refs.resolve_tag(f"file://{local_repo}", "v2.0.0") == local_repo_head


def _sha_of(repo, tag):
    import subprocess
    out = subprocess.run(["git", "rev-list", "-n1", tag], cwd=repo,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_release_picks_the_newest_matching_tag(tagged_repo):
    url = f"file://{tagged_repo}"
    got = refs.resolve_for(url, {"release": "^1.2"})
    assert got["tag"] == "v1.10.0", "string ordering would wrongly pick v1.9.0"
    assert got["release"] == "^1.2"
    assert got["rev"] == _sha_of(tagged_repo, "v1.10.0")


def test_release_ignores_prereleases_and_non_versions(tagged_repo):
    got = refs.resolve_for(f"file://{tagged_repo}", {"release": "*"})
    assert got["tag"] == "v1.10.0"       # not v2.0.0-rc1, not nixos-24.05


def test_release_that_matches_nothing_lists_what_there_was(tagged_repo):
    with pytest.raises(refs.RefError) as e:
        refs.resolve_for(f"file://{tagged_repo}", {"release": "^9"})
    msg = str(e.value)
    assert "no tag satisfies" in msg and "v1.10.0" in msg


def test_release_resolves_to_a_commit_not_a_tag_object(tagged_repo):
    """Every tag in this fixture is annotated, so an unpeeled implementation
    returns a sha that is well-formed and useless."""
    got = refs.resolve_for(f"file://{tagged_repo}", {"release": "^1.2"})
    assert got["rev"] == _sha_of(tagged_repo, "v1.10.0")


def test_strategy_precedence(local_repo, local_repo_head):
    url = f"file://{local_repo}"
    assert refs.resolve_for(url, {"rev": "f" * 40})["rev"] == "f" * 40
    assert refs.resolve_for(url, {"tag": "v2.0.0"})["rev"] == local_repo_head
    got = refs.resolve_for(url, {"ref": "main"})
    assert got == {"rev": local_repo_head, "ref": "main"}
    assert refs.resolve_for(url, {}) == {"rev": local_repo_head, "ref": "HEAD"}


def test_the_implicit_head_case_is_recorded_as_a_ref(local_repo, local_repo_head):
    """Not cosmetic. Without a ref, the git fetcher must pass `allRefs = true`
    so fetchGit can find the rev, and that refetches every ref on the remote on
    every evaluation -- measured at 0.40-3.29 s and erratic against 0.05-0.07 s
    and stable with a ref."""
    got = refs.resolve_for(f"file://{local_repo}", {})
    assert got == {"rev": local_repo_head, "ref": "HEAD"}


def test_an_explicit_rev_records_no_ref(local_repo):
    """pnix does not know which branch someone else's rev lives on, so allRefs
    stays the only way to find it."""
    assert refs.resolve_for(f"file://{local_repo}", {"rev": "f" * 40}) == {"rev": "f" * 40}


def test_an_explicit_rev_keeps_what_it_was_declared_alongside():
    """`rev` wins, but dropping the strategy it accompanied meant a pin frozen
    at a pull-request head recorded the rev and nothing about the PR -- the one
    thing a reader needs later, and exactly the gap that made tack's lock
    unusable as a source when translating declarations."""
    out = refs.resolve_for("u", {"rev": "a" * 40, "ref": "refs/pull/181/head"})
    assert out == {"rev": "a" * 40, "ref": "refs/pull/181/head"}

    assert refs.resolve_for("u", {"rev": "b" * 40, "tag": "v1.2.3"}) == {
        "rev": "b" * 40, "tag": "v1.2.3",
    }
    assert refs.resolve_for("u", {"rev": "c" * 40, "release": "^1.2"}) == {
        "rev": "c" * 40, "release": "^1.2",
    }


def test_an_explicit_rev_alone_still_records_only_the_rev():
    assert refs.resolve_for("u", {"rev": "d" * 40}) == {"rev": "d" * 40}


def test_an_explicit_rev_needs_no_network(monkeypatch):
    """It is the one strategy that answers without asking the remote."""
    monkeypatch.setattr(refs, "_ls_remote",
                        lambda *a: (_ for _ in ()).throw(AssertionError("network")))
    assert refs.resolve_for("u", {"rev": "e" * 40, "ref": "main"})["rev"] == "e" * 40


# --- ref cache integration ------------------------------------------------
#
# The seam is `_ls_remote`, the single place all three lookup shapes go through,
# so `resolve`, `resolve_tag` and `tags` are all covered by wiring one function.

def _counting_git(monkeypatch, stdout: str):
    calls = []

    class Proc:
        returncode = 0
        stderr = ""

        def __init__(self):
            self.stdout = stdout

    def run(cmd, **kw):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr("subprocess.run", run)
    return calls


def test_without_a_cache_every_call_hits_the_remote(monkeypatch):
    calls = _counting_git(monkeypatch, f"{'a' * 40}\trefs/heads/main\n")
    refs.resolve("https://x/y", "main")
    refs.resolve("https://x/y", "main")
    assert len(calls) == 2


def test_with_a_cache_the_second_call_is_free(monkeypatch, tmp_path):
    from pnix import refcache

    calls = _counting_git(monkeypatch, f"{'a' * 40}\trefs/heads/main\n")
    cache = refcache.Cache(tmp_path / "refs.json")
    monkeypatch.setattr(refs, "CACHE", cache)
    assert refs.resolve("https://x/y", "main") == "a" * 40
    assert refs.resolve("https://x/y", "main") == "a" * 40
    assert len(calls) == 1


def test_a_cached_answer_is_interpreted_the_same_way(monkeypatch, tmp_path):
    """Rows are cached, not a chosen rev, so `_pick`'s precedence runs on the
    cached path exactly as it does live. An annotated tag is the case that would
    break if a rev were cached instead: the peeled row must still win."""
    from pnix import refcache

    stdout = (f"{'b' * 40}\trefs/tags/v1\n"
              f"{'c' * 40}\trefs/tags/v1^{{}}\n")
    calls = _counting_git(monkeypatch, stdout)
    monkeypatch.setattr(refs, "CACHE", refcache.Cache(tmp_path / "refs.json"))
    live = refs.resolve("https://x/y", "v1")
    cached = refs.resolve("https://x/y", "v1")
    assert live == cached == "c" * 40
    assert len(calls) == 1


def test_a_failure_is_not_cached(monkeypatch, tmp_path):
    """A typo'd ref must error every run, and a transient network failure must
    not stick for an hour."""
    from pnix import refcache

    calls = []

    class Proc:
        returncode = 128
        stdout = ""
        stderr = "fatal: could not read from remote"

    def run(cmd, **kw):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr(refs, "CACHE", refcache.Cache(tmp_path / "refs.json"))
    for _ in range(2):
        with pytest.raises(refs.RefError):
            refs.resolve("https://x/y", "nope")
    assert len(calls) == 2


def test_an_empty_answer_is_not_cached(monkeypatch, tmp_path):
    """`ls-remote` exiting 0 with no rows means the ref does not exist. Caching
    that would hide a branch appearing for a whole TTL."""
    from pnix import refcache

    calls = _counting_git(monkeypatch, "")
    monkeypatch.setattr(refs, "CACHE", refcache.Cache(tmp_path / "refs.json"))
    for _ in range(2):
        with pytest.raises(refs.RefError):
            refs.resolve("https://x/y", "main")
    assert len(calls) == 2
