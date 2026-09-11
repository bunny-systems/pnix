"""Repository URLs: the one thing a declaration has to say.

A pin used to name `owner`, `repo` and sometimes `host`, which meant the
declaration described pnix's internal model of a forge rather than the thing
being fetched. A URL is what a user already has -- it is in their browser, in
the project's README, in `git remote -v` -- and it carries all three.

`type` still selects *how* the bytes are obtained, because that cannot be read
off a URL: the same `https://github.com/o/r` is a codeload archive under
`github` and a clone under `git`. What `type` no longer has to do is repeat
information the URL already contains.
"""

import re

#: host -> source type, for hosts whose software is known. A self-hosted
#: instance cannot be guessed and must say `type` itself.
HOSTS = {
    "github.com": "github",
    "gitlab.com": "gitlab",
    "codeberg.org": "forgejo",
    "git.sr.ht": "sourcehut",
}

# scheme://host/path, git@host:path, or a bare host/path.
_URL = re.compile(
    r"^(?:(?P<scheme>[a-z][a-z0-9+.-]*)://)?"
    r"(?:(?P<user>[^@/]+)@)?"
    r"(?P<host>[^/:]+)"
    r"[:/]"
    r"(?P<path>.+?)"
    r"(?:\.git)?/?$"
)


class UrlError(Exception):
    pass


def parse(url: str) -> tuple[str, str, str]:
    """`url` -> (host, owner, repo).

    Owner keeps whatever the URL has, `~` included: sourcehut's
    `https://git.sr.ht/~sircmpwn/scdoc` is owner `~sircmpwn`. A tilde that
    appeared from nowhere would be pnix inventing part of an address.
    """
    m = _URL.match(url.strip())
    if m is None:
        raise UrlError(f"{url!r} is not a repository URL")

    parts = [p for p in m.group("path").split("/") if p]
    if len(parts) < 2:
        raise UrlError(
            f"{url!r} has no owner/repo path; a repository URL looks like "
            f"https://host/owner/repo"
        )
    # Deeper paths are a subgroup (gitlab) or a browse URL; the last two
    # segments are always owner and repo.
    return m.group("host"), "/".join(parts[:-1]), parts[-1]


def infer_type(url: str) -> str | None:
    """The source type implied by the host, or None when it is unknown.

    None is not a failure: it means the caller must have been told a `type`.
    Guessing that an unknown host runs Forgejo is how you get a 404 at lock
    time and a confusing one at that.
    """
    try:
        host, _, _ = parse(url)
    except UrlError:
        return None
    return HOSTS.get(host)


def clone_url(url: str) -> str:
    """The URL to hand `git ls-remote`. Normalised, never guessed."""
    host, owner, repo = parse(url)
    scheme = url.split("://", 1)[0] if "://" in url else "https"
    if scheme in ("ssh", "git+ssh"):
        return f"ssh://{host}/{owner}/{repo}"
    return f"https://{host}/{owner}/{repo}"
