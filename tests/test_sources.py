import pytest

from pnix import sources
from pnix.sources import base


def test_registry_exposes_github():
    assert sources.get("github").type == "github"


def test_unknown_type_raises_listing_known_types():
    with pytest.raises(base.UnknownSource) as e:
        sources.get("mercurial")
    assert "github" in str(e.value)


def test_github_resolve_produces_locked_node_without_hash(monkeypatch):
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "b" * 40)
    src = sources.get("github")
    locked = src.resolve({"type": "github", "owner": "NixOS",
                          "repo": "nixpkgs", "ref": "nixos-unstable"})
    assert locked == {
        "type": "github", "host": "github.com", "owner": "NixOS",
        "repo": "nixpkgs", "ref": "nixos-unstable", "rev": "b" * 40,
    }
    assert "hash" not in locked


def test_github_honours_custom_host(monkeypatch):
    seen = {}

    def fake(url, ref=None):
        seen["url"] = url
        return "c" * 40

    monkeypatch.setattr("pnix.refs.resolve", fake)
    sources.get("github").resolve(
        {"type": "github", "host": "forgejo.nimeses.com",
         "owner": "n", "repo": "r"})
    assert seen["url"] == "https://forgejo.nimeses.com/n/r"


def test_github_prefetch_uses_codeload_tarball(monkeypatch):
    seen = {}

    def fake(url):
        seen["url"] = url
        return "sha256-XYZ", 1788914643

    monkeypatch.setattr("pnix.prefetch.tarball", fake)
    got = sources.get("github").prefetch(
        {"type": "github", "host": "github.com", "owner": "NixOS",
         "repo": "nixpkgs", "rev": "d" * 40})
    assert got == {"hash": "sha256-XYZ", "lastModified": 1788914643}
    assert seen["url"] == (
        "https://github.com/NixOS/nixpkgs/archive/" + "d" * 40 + ".tar.gz")


def test_github_prefetch_survives_an_archive_with_no_mtime(monkeypatch):
    monkeypatch.setattr("pnix.prefetch.tarball",
                        lambda url: ("sha256-XYZ", None))
    got = sources.get("github").prefetch(
        {"host": "github.com", "owner": "o", "repo": "r", "rev": "d" * 40})
    assert got == {"hash": "sha256-XYZ"}


def test_git_resolve_keeps_url_and_flags(monkeypatch):
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "e" * 40)
    locked = sources.get("git").resolve({
        "type": "git",
        "url": "https://github.com/hyprwm/Hyprland.git",
        "ref": "main",
        "submodules": True,
    })
    assert locked == {
        "type": "git",
        "url": "https://github.com/hyprwm/Hyprland.git",
        "ref": "main",
        "rev": "e" * 40,
        "submodules": True,
    }


def test_git_omits_false_flags(monkeypatch):
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "f" * 40)
    locked = sources.get("git").resolve(
        {"type": "git", "url": "https://example.com/r.git"})
    assert "submodules" not in locked
    assert "shallow" not in locked


def test_git_prefetch_adds_nothing_because_rev_is_the_hash(local_repo,
                                                           local_repo_head):
    locked = {"type": "git", "url": f"file://{local_repo}",
              "rev": local_repo_head}
    assert sources.get("git").prefetch(locked) == {}


# --- the forge family ------------------------------------------------------
#
# Every one of these emits `kind = "tarball"`, so none of them added a file to
# the vendored resolver. That is the whole point of keying fetching on
# primitives rather than on source types.

@pytest.mark.parametrize(
    ("type_", "expected"),
    [
        ("github", "https://github.com/o/r/archive/REV.tar.gz"),
        ("forgejo", "https://codeberg.org/o/r/archive/REV.tar.gz"),
        ("gitea", "https://codeberg.org/o/r/archive/REV.tar.gz"),
        ("gitlab", "https://gitlab.com/o/r/-/archive/REV/r-REV.tar.gz"),
        ("sourcehut", "https://git.sr.ht/o/r/archive/REV.tar.gz"),
    ],
)
def test_each_forge_builds_its_own_archive_url(type_, expected):
    src = sources.get(type_)
    assert src.archive_url({"owner": "o", "repo": "r", "rev": "REV"}) == expected
    assert src.kinds == ("tarball",)


@pytest.mark.parametrize("type_",
                         ["github", "forgejo", "gitea", "gitlab", "sourcehut"])
def test_every_forge_resolves_through_ls_remote(type_, monkeypatch):
    seen = {}

    def fake(url, ref=None):
        seen["url"] = url
        return "a" * 40

    monkeypatch.setattr("pnix.refs.resolve", fake)
    locked = sources.get(type_).resolve(
        {"type": type_, "owner": "o", "repo": "r", "ref": "main"})
    assert locked["rev"] == "a" * 40
    assert locked["ref"] == "main"
    assert seen["url"].endswith("/o/r")


def test_a_custom_host_reaches_both_urls(monkeypatch):
    monkeypatch.setattr("pnix.refs.resolve", lambda url, ref=None: "b" * 40)
    src = sources.get("forgejo")
    locked = src.resolve({"host": "forgejo.nimeses.com", "owner": "n", "repo": "x"})
    assert locked["host"] == "forgejo.nimeses.com"
    assert src.archive_url(locked).startswith("https://forgejo.nimeses.com/n/x/archive/")


# --- url-shaped sources ----------------------------------------------------

def test_tarball_pins_the_url_the_redirect_leads_to(monkeypatch):
    """A declaration may name a moving target; the lock must not."""
    monkeypatch.setattr("pnix.prefetch.resolve_redirect",
                        lambda url: "https://cdn/real-1.2.3.tar.gz")
    monkeypatch.setattr("pnix.prefetch.tarball",
                        lambda url: ("sha256-T", 1788914643))
    src = sources.get("tarball")
    locked = src.resolve({"type": "tarball", "url": "https://e/latest.tar.gz"})
    assert locked["url"] == "https://e/latest.tar.gz"          # provenance
    assert locked["resolvedUrl"] == "https://cdn/real-1.2.3.tar.gz"
    locked.update(src.prefetch(locked))
    assert src.fetch_spec(locked) == {
        "kind": "tarball", "url": "https://cdn/real-1.2.3.tar.gz", "hash": "sha256-T"}


def test_file_uses_the_flat_hash_primitive(monkeypatch):
    monkeypatch.setattr("pnix.prefetch.resolve_redirect", lambda url: url)
    monkeypatch.setattr("pnix.prefetch.file", lambda url: "sha256-F")
    src = sources.get("file")
    locked = src.resolve({"type": "file", "url": "https://e/x.bin"})
    locked.update(src.prefetch(locked))
    assert src.fetch_spec(locked)["kind"] == "file"
    assert "lastModified" not in locked


def test_channel_follows_its_pointer_and_keeps_the_version(monkeypatch):
    resolved = ("https://releases.nixos.org/nixos/unstable/"
                "nixos-26.11pre1070770.8ce4ef6cb6f8/nixexprs.tar.xz")
    monkeypatch.setattr("pnix.prefetch.resolve_redirect", lambda url: resolved)
    monkeypatch.setattr("pnix.prefetch.tarball", lambda url: ("sha256-C", 1))
    src = sources.get("channel")
    locked = src.resolve({"type": "channel", "channel": "nixos-unstable"})
    assert locked["resolvedUrl"] == resolved
    # The only rev-shaped thing a channel pin has.
    assert locked["version"] == "nixos-26.11pre1070770.8ce4ef6cb6f8"
    locked.update(src.prefetch(locked))
    assert src.fetch_spec(locked)["kind"] == "tarball"


def test_path_carries_no_hash():
    src = sources.get("path")
    locked = src.resolve({"type": "path", "path": "/home/me/checkout"})
    assert src.prefetch(locked) == {}
    assert src.fetch_spec(locked) == {"kind": "path", "path": "/home/me/checkout"}


def test_the_registry_lists_every_type_on_a_typo():
    with pytest.raises(base.UnknownSource) as e:
        sources.get("mercurial")
    msg = str(e.value)
    for t in ("github", "gitlab", "sourcehut", "tarball", "channel", "path"):
        assert t in msg


def test_a_rev_pinned_git_source_still_gets_a_ref_when_it_is_head(
        local_repo, local_repo_head):
    """An explicit rev leaves no ref, and fetchGit then needs allRefs -- which
    refetches every ref on every evaluation. When the rev is HEAD, one
    ls-remote at lock time avoids that forever."""
    locked = sources.get("git").resolve(
        {"type": "git", "url": f"file://{local_repo}", "rev": local_repo_head})
    assert locked["ref"] == "HEAD"
    assert sources.get("git").fetch_spec(locked)["ref"] == "HEAD"


def test_a_rev_that_is_not_head_keeps_allrefs(local_repo, monkeypatch):
    """pnix does not know which branch an arbitrary rev lives on; guessing
    would produce a lock that cannot fetch."""
    locked = sources.get("git").resolve(
        {"type": "git", "url": f"file://{local_repo}", "rev": "9" * 40})
    assert "ref" not in locked
    assert "ref" not in sources.get("git").fetch_spec(locked)


def test_git_carries_every_declared_flag_into_the_fetch_node():
    """FLAGS is the whole of pnix's fetchGit boolean surface, and the fetcher
    forwards a node wholesale -- so extending this tuple is all it takes to
    support a new option. `lfs` and `exportIgnore` earn their place: without
    the first an LFS repo yields pointer files, and without the second a git
    pin and a forge tarball of the same rev give different trees."""
    from pnix.sources.git import FLAGS, Git

    locked = {"type": "git", "url": "u", "rev": "r", "ref": "main"}
    locked.update(dict.fromkeys(FLAGS, True))
    spec = Git().fetch_spec(locked)
    assert {"lfs", "exportIgnore"} <= set(FLAGS)
    for flag in FLAGS:
        assert spec[flag] is True, flag


def test_git_omits_flags_that_were_not_declared():
    from pnix.sources.git import FLAGS, Git

    spec = Git().fetch_spec({"type": "git", "url": "u", "rev": "r"})
    assert not (set(FLAGS) & set(spec))
