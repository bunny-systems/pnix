"""git.sr.ht.

Owner includes the leading `~`, because that is what the URL contains:
`owner = "~sircmpwn"; repo = "scdoc";`. pnix does not add it -- a declaration
that means `~user` should say so, rather than have a tilde appear from nowhere.
"""

from pnix.sources.forge import ArchiveForge


class SourceHut(ArchiveForge):
    type = "sourcehut"
    default_host = "git.sr.ht"
    forge = None

    def archive_url(self, locked: dict) -> str:
        host = locked.get("host") or self.default_host
        return (f"https://{host}/{locked['owner']}/{locked['repo']}"
                f"/archive/{locked['rev']}.tar.gz")
