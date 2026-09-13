"""Forges that serve a source archive at a predictable URL.

github, gitlab, forgejo/gitea and sourcehut differ in exactly two ways: where
the archive lives, and what the clone URL looks like. Everything else -- resolve
a ref with `git ls-remote`, fetch the archive, hash the unpacked NAR, read the
commit date out of the tarball -- is identical, so it lives here and a new forge
is a subclass with two URL methods.

This is the payoff of the schema-2 decision to key fetching on primitives rather
than on source types. Every forge below emits `kind = "tarball"`, so none of
them adds a file to the vendored resolver or costs anyone a `pnix init`.
"""

from pnix import prefetch, refs, urls


class ArchiveForge:
    #: source type name, and the `type` recorded in the lock
    type: str = ""
    #: host used when the declaration does not name one
    default_host: str = ""
    #: which pnix.forges client knows this forge's PR API, if any
    forge: str | None = None

    kinds = ("tarball",)
    prefetch_keys = ("hash", "lastModified")

    def archive_url(self, locked: dict) -> str:
        raise NotImplementedError

    def resolve(self, spec: dict) -> dict:
        """`host`, `owner` and `repo` are read off the URL, not declared.

        They stay in the lock as provenance -- `pnix look` prints them and a
        human reads them -- but they are derived, so a declaration cannot say
        one thing and the URL another.
        """
        host, owner, repo = urls.parse(spec["url"])
        locked = {
            "type": self.type,
            "host": host,
            "owner": owner,
            "repo": repo,
            "url": urls.clone_url(spec["url"]),
        }
        locked.update(refs.resolve_for(locked["url"], spec))
        return locked

    def prefetch(self, locked: dict) -> dict:
        sri, mtime = prefetch.tarball(self.archive_url(locked))
        out: dict = {"hash": sri}
        if mtime is not None:
            out["lastModified"] = mtime
        return out

    def fetch_spec(self, locked: dict) -> dict:
        return {
            "kind": "tarball",
            "url": self.archive_url(locked),
            "hash": locked["hash"],
        }
