"""Sources that are just a URL: `tarball` and `file`.

The two differ only in whether the bytes are unpacked, and that difference runs
all the way down: `tarball` hashes the NAR of the unpacked tree, `file` hashes
the bytes. They are separate fetch primitives for exactly that reason, and
feeding one's hash to the other fails at fetch time with a mismatch that says
nothing about the cause.

Both follow redirects at lock time, so a declaration may name a moving target
and the lock still records the concrete artefact behind it. The declared URL
survives as provenance; `fetch.url` is the resolved one.
"""

from pnix import prefetch


class _Url:
    kinds: tuple[str, ...] = ()

    def resolve(self, spec: dict) -> dict:
        return {
            "type": self.type,
            "url": spec["url"],
            "resolvedUrl": prefetch.resolve_redirect(spec["url"]),
        }

    def fetch_spec(self, locked: dict) -> dict:
        return {
            "kind": self.kinds[0],
            "url": locked.get("resolvedUrl") or locked["url"],
            "hash": locked["hash"],
        }


class Tarball(_Url):
    type = "tarball"
    kinds = ("tarball",)

    def prefetch(self, locked: dict) -> dict:
        url = locked.get("resolvedUrl") or locked["url"]
        sri, mtime = prefetch.tarball(url)
        out: dict = {"hash": sri}
        if mtime is not None:
            out["lastModified"] = mtime
        return out


class File(_Url):
    type = "file"
    kinds = ("file",)

    def prefetch(self, locked: dict) -> dict:
        url = locked.get("resolvedUrl") or locked["url"]
        return {"hash": prefetch.file(url)}
