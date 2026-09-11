"""Plain git sources, fetched with builtins.fetchGit at eval time.

fetchGit is content-addressed by revision, so there is no separate hash to
store: a git rev is already a cryptographic commitment to the tree. This is
the type to use when submodules are needed, since forge tarballs omit them.

**A ref is worth recording even when the declaration had none.** fetchGit needs
a ref to locate a rev; lacking one it must be given `allRefs = true`, which
refetches every ref on the remote on every evaluation. Measured on codeberg
niri-nix: 0.40-3.29 s and erratic against 0.05-0.07 s and stable. refs.resolve_for
records `ref = "HEAD"` for the implicit case so that fallback is rarely reached.
"""

from pnix import refs

# Boolean fetchGit options carried straight through to the builtin. The
# fetcher is a flat passthrough, so extending this tuple is the whole of adding
# one -- nothing in the vendored resolver changes.
#
#   lfs          without it an LFS repo yields pointer files, not content, and
#                nothing warns; the build fails later on a 130-byte "binary".
#   exportIgnore fetchGit ignores .gitattributes export-ignore by default while
#                a forge tarball honours it. Same rev, different tree. This is
#                how a git pin and a tarball pin of one repo disagree.
FLAGS = ("submodules", "shallow", "lfs", "exportIgnore")


class Git:
    type = "git"
    kinds = ("git",)

    def resolve(self, spec: dict) -> dict:
        locked = {"type": self.type, "url": spec["url"]}
        locked.update(refs.resolve_for(spec["url"], spec))

        if "ref" not in locked:
            try:
                if refs.resolve(spec["url"]) == locked["rev"]:
                    locked["ref"] = "HEAD"
            except refs.RefError:
                pass

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
