"""Plain git sources, fetched with builtins.fetchGit at eval time.

fetchGit is content-addressed by revision, so there is no separate hash to
store: a git rev is already a cryptographic commitment to the tree. This is
the type to use when submodules are needed, since forge tarballs omit them.
"""

from pnix import refs

FLAGS = ("submodules", "shallow")


class Git:
    type = "git"
    kinds = ("git",)

    def resolve(self, spec: dict) -> dict:
        locked = {"type": self.type, "url": spec["url"]}
        locked.update(refs.resolve_for(spec["url"], spec))
        for flag in FLAGS:
            if spec.get(flag):
                locked[flag] = True
        return locked

    def prefetch(self, locked: dict) -> dict:
        """Nothing to add: the rev is the integrity guarantee, and fetchGit
        reports rev, lastModified and narHash itself at eval time."""
        return {}

    def fetch_spec(self, locked: dict) -> dict:
        spec = {"kind": "git", "url": locked["url"], "rev": locked["rev"]}
        # A tag is a ref fetchGit can use to find the rev without allRefs.
        ref = locked.get("ref") or (
            f"refs/tags/{locked['tag']}" if "tag" in locked else None)
        if ref:
            spec["ref"] = ref
        for flag in FLAGS:
            if locked.get(flag):
                spec[flag] = True
        return spec
