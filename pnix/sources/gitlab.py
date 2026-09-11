"""gitlab.com and self-hosted GitLab.

The archive path is unlike every other forge here: `/-/archive/<rev>/<name>.tar.gz`,
where the trailing filename chooses the format and names the top-level directory.
The short form `/-/archive/<rev>.tar.gz` also answers 200, but the long form is
the one GitLab's own UI emits, so that is what pnix asks for.

Note this is a *source* type only. GitLab's merge-request API was never probed,
so `pnix.forges` has no gitlab client and a `{ pr = N; }` patch on a gitlab pin
is refused rather than guessed at.
"""

from pnix.sources.forge import ArchiveForge


class GitLab(ArchiveForge):
    type = "gitlab"
    default_host = "gitlab.com"
    forge = None

    def archive_url(self, locked: dict) -> str:
        host = locked.get("host") or self.default_host
        repo, rev = locked["repo"], locked["rev"]
        return (f"https://{host}/{locked['owner']}/{repo}"
                f"/-/archive/{rev}/{repo}-{rev}.tar.gz")
