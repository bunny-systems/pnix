"""Repository URL parsing.

The declaration language rests on this: if `parse` gets a shape wrong, a pin
fetches from the wrong place or fails to resolve at all. Every case below is a
URL that appears in a real pin somewhere.
"""

import pytest

from pnix import urls


@pytest.mark.parametrize(
    ("url", "want"),
    [
        ("https://github.com/feel-co/hjem", ("github.com", "feel-co", "hjem")),
        # `git remote -v` and forge "clone" buttons both hand you a .git suffix.
        ("https://github.com/hyprwm/Hyprland.git",
         ("github.com", "hyprwm", "Hyprland")),
        ("https://codeberg.org/BANanaD3V/niri-nix",
         ("codeberg.org", "BANanaD3V", "niri-nix")),
        # Sourcehut owners carry a `~`. pnix keeps whatever the URL has rather
        # than inventing part of an address.
        ("https://git.sr.ht/~sircmpwn/scdoc", ("git.sr.ht", "~sircmpwn", "scdoc")),
        # GitLab subgroups make the path deeper than two segments.
        ("https://gitlab.com/group/sub/proj", ("gitlab.com", "group/sub", "proj")),
        ("git@github.com:o/r.git", ("github.com", "o", "r")),
        ("ssh://git@example.com/o/r", ("example.com", "o", "r")),
        ("https://github.com/o/r/", ("github.com", "o", "r")),
    ],
)
def test_parse(url, want):
    assert urls.parse(url) == want


@pytest.mark.parametrize("url", ["https://github.com", "notaurl", ""])
def test_a_url_with_no_owner_repo_is_refused(url):
    with pytest.raises(urls.UrlError):
        urls.parse(url)


@pytest.mark.parametrize(
    ("url", "want"),
    [
        ("https://github.com/o/r", "github"),
        ("https://gitlab.com/o/r", "gitlab"),
        # Codeberg runs Forgejo. Knowing that is what gives a Codeberg pin a
        # tarball fetch and PR support instead of a bare clone.
        ("https://codeberg.org/o/r", "forgejo"),
        ("https://git.sr.ht/~o/r", "sourcehut"),
        ("https://forgejo.nimeses.com/o/r", None),
        ("nonsense", None),
    ],
)
def test_infer_type(url, want):
    assert urls.infer_type(url) == want


def test_clone_url_is_normalised_never_guessed():
    assert urls.clone_url("https://github.com/o/r.git") == "https://github.com/o/r"
    assert urls.clone_url("git@github.com:o/r.git") == "https://github.com/o/r"
    assert urls.clone_url("ssh://git@example.com/o/r") == "ssh://example.com/o/r"


def test_the_host_to_forge_table_is_derived_not_repeated():
    """Three tables used to say overlapping things: which software a host runs
    (urls.HOSTS), which PR client that software has (Source.forge), and which
    PR client a host has (forges.BY_HOST). The third is the first two composed,
    so adding a host in one place and forgetting the other silently cost that
    host its PR support."""
    from pnix import forges, sources

    for host, type_name in urls.HOSTS.items():
        want = getattr(sources.get(type_name), "forge", None)
        assert forges.for_host(host) == want, host


def test_a_host_with_no_pr_client_reports_none():
    """GitLab and sourcehut can be fetched from but have no PR client in pnix,
    so `{ pr = N; }` against them is refused rather than guessed."""
    from pnix import forges

    assert forges.for_host("gitlab.com") is None
    assert forges.for_host("git.sr.ht") is None
    assert forges.for_host("forgejo.nimeses.com") is None
