"""The ref cache: what makes an entry still good, and what must never be cached.

There is no cheap way to ask a remote "has this moved?" -- measured, a
conditional request with an ETag still costs a GitHub rate-limit unit, and
`git ls-remote` has no conditional form at all. Resolving *is* the check, so
the cache can only decide from age and from what the user explicitly asked for.
"""

import json
import time

from pnix import refcache


def test_a_miss_returns_none(tmp_path):
    c = refcache.Cache(tmp_path / "refs.json")
    assert c.get(("https://x/y", ("main",))) is None


def test_a_stored_entry_comes_back(tmp_path):
    c = refcache.Cache(tmp_path / "refs.json")
    c.put(("https://x/y", ("main",)), [["a" * 40, "refs/heads/main"]])
    assert c.get(("https://x/y", ("main",))) == [["a" * 40, "refs/heads/main"]]


def test_a_different_key_does_not_hit(tmp_path):
    c = refcache.Cache(tmp_path / "refs.json")
    c.put(("https://x/y", ("main",)), [["a" * 40, "refs/heads/main"]])
    assert c.get(("https://x/y", ("other",))) is None
    assert c.get(("https://other/y", ("main",))) is None


def test_an_entry_past_its_ttl_is_a_miss(tmp_path):
    c = refcache.Cache(tmp_path / "refs.json", ttl=10)
    key = ("https://x/y", ("main",))
    c.put(key, [["a" * 40, "refs/heads/main"]])
    c._entries[c._hash(key)]["at"] = time.time() - 11
    assert c.get(key) is None


def test_an_entry_from_the_future_is_a_miss(tmp_path):
    """A clock that moved backwards would otherwise pin a rev for the whole TTL
    plus however far the clock jumped."""
    c = refcache.Cache(tmp_path / "refs.json", ttl=3600)
    key = ("https://x/y", ("main",))
    c.put(key, [["a" * 40, "refs/heads/main"]])
    c._entries[c._hash(key)]["at"] = time.time() + 600
    assert c.get(key) is None


def test_it_survives_a_round_trip_through_disk(tmp_path):
    path = tmp_path / "refs.json"
    a = refcache.Cache(path)
    a.put(("https://x/y", ("main",)), [["a" * 40, "refs/heads/main"]])
    a.save()
    assert refcache.Cache(path).get(("https://x/y", ("main",))) == [
        ["a" * 40, "refs/heads/main"]
    ]


def test_a_corrupt_file_reads_as_empty_rather_than_failing(tmp_path):
    path = tmp_path / "refs.json"
    path.write_text("{not json at all")
    c = refcache.Cache(path)
    assert c.get(("https://x/y", ("main",))) is None


def test_a_cache_from_another_version_is_ignored(tmp_path):
    """Same lesson as the lock's `schema` and the vendored-copy check: a file
    this pnix cannot read is discarded, never guessed at."""
    path = tmp_path / "refs.json"
    path.write_text(json.dumps({
        "version": refcache.VERSION + 1,
        "entries": {"whatever": {"at": time.time(), "rows": []}},
    }))
    c = refcache.Cache(path)
    assert c.get(("https://x/y", ("main",))) is None


def test_saving_leaves_no_temporary_file_behind(tmp_path):
    path = tmp_path / "refs.json"
    c = refcache.Cache(path)
    c.put(("https://x/y", ("main",)), [["a" * 40, "refs/heads/main"]])
    c.save()
    assert [p.name for p in tmp_path.iterdir()] == ["refs.json"]


def test_a_save_with_nothing_new_writes_nothing(tmp_path):
    """Eight threads share one file; a run that only read must not rewrite it."""
    path = tmp_path / "refs.json"
    refcache.Cache(path).save()
    assert not path.exists()


def test_ages_reports_what_came_from_cache(tmp_path):
    """`look` exists to report drift, so a cached answer has to be visible --
    silently stale is the one failure mode it cannot have."""
    c = refcache.Cache(tmp_path / "refs.json", ttl=3600)
    key = ("https://x/y", ("main",))
    c.put(key, [["a" * 40, "refs/heads/main"]])
    c._entries[c._hash(key)]["at"] = time.time() - 120
    assert c.get(key) is not None
    assert c.ages and 115 < next(iter(c.ages.values())) < 125


def test_the_default_location_is_the_xdg_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert refcache.default_path() == tmp_path / "pnix" / "refs.json"


def test_without_xdg_it_falls_back_to_dot_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert refcache.default_path() == tmp_path / ".cache" / "pnix" / "refs.json"


def test_an_undeterminable_home_disables_the_cache_instead_of_raising(monkeypatch):
    """The module's promise is that a cache is never worth failing a run over,
    and that has to include not being able to find where it would live. No HOME
    and no passwd entry -- a scratch container -- makes `Path.home()` raise."""
    def boom():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", staticmethod(boom))
    assert refcache.default_path() is None
    assert refcache.open_default() is None
