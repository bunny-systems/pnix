"""Forgejo and Gitea: codeberg.org, and forgejo.nimeses.com.

The archive path is the same as GitHub's, so this could have been `type =
"github"` with a `host`. It is its own type because `forge` is not cosmetic:
patches key on it, and Forgejo's PR endpoints differ from GitHub's -- it has no
compare diff at all (verified 2026-09-11).
"""

from pnix.sources.forge import ArchiveForge


class Forgejo(ArchiveForge):
    type = "forgejo"
    default_host = "codeberg.org"
    forge = "forgejo"

    def archive_url(self, locked: dict) -> str:
        host = locked.get("host") or self.default_host
        return (f"https://{host}/{locked['owner']}/{locked['repo']}"
                f"/archive/{locked['rev']}.tar.gz")


class Gitea(Forgejo):
    """Same API and the same archive path; Forgejo is a Gitea fork."""

    type = "gitea"
