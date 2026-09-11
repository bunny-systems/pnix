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
    assert refs.resolve_for(url, {}) == {"rev": local_repo_head}
