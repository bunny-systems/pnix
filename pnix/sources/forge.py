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

from pnix import prefetch, refs


class ArchiveForge:
    #: source type name, and the `type` recorded in the lock
    type: str = ""
    #: host used when the declaration does not name one
    default_host: str = ""
    #: which pnix.forges client knows this forge's PR API, if any
    forge: str | None = None

    kinds = ("tarball",)

    def host(self, spec: dict) -> str:
        return spec.get("host") or self.default_host

    def clone_url(self, spec: dict) -> str:
        return f"https://{self.host(spec)}/{spec['owner']}/{spec['repo']}"

    def archive_url(self, locked: dict) -> str:
        raise NotImplementedError

    def resolve(self, spec: dict) -> dict:
        locked = {
            "type": self.type,
            "host": self.host(spec),
            "owner": spec["owner"],
            "repo": spec["repo"],
        }
        locked.update(refs.resolve_for(self.clone_url(spec), spec))
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
