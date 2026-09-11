"""github.com, and GitHub Enterprise via `host`.

Uses the codeload tarball rather than a clone: far less traffic, and the hash is
of the unpacked NAR so upstream recompression is irrelevant.
"""

from pnix.sources.forge import ArchiveForge


class GitHub(ArchiveForge):
    type = "github"
    default_host = "github.com"
    forge = "github"

    def archive_url(self, locked: dict) -> str:
        host = locked.get("host") or self.default_host
        return (f"https://{host}/{locked['owner']}/{locked['repo']}"
                f"/archive/{locked['rev']}.tar.gz")
