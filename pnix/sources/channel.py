"""A Nix channel: `channel = "nixos-unstable"`.

A channel is a pointer, so resolving one means following it. Measured:

    https://channels.nixos.org/nixos-unstable/nixexprs.tar.xz
      -> https://releases.nixos.org/nixos/unstable/nixos-26.11pre1070770.8ce4ef6cb6f8/nixexprs.tar.xz

The resolved URL names the exact nixpkgs revision, which is why the version
string is kept as provenance -- it is the only rev-shaped thing a channel pin
has.

**`.tar.xz`, which `fetchTarball` unpacks.** The design left open whether a
channel could be used at all, on the assumption that channels serve
`nixexprs.tar.zst` and that `fetchTarball` cannot handle zstd. channels.nixos.org
serves xz, so the question does not arise.

A channel tarball is a prebuilt nixpkgs expression tree, not a git checkout: no
`rev`, and its `lastModified` is whatever the archive carries.
"""

import re

from pnix import prefetch

BASE = "https://channels.nixos.org"


class Channel:
    type = "channel"
    kinds = ("tarball",)

    def _url(self, spec: dict) -> str:
        base = spec.get("url") or BASE
        return f"{base}/{spec['channel']}/nixexprs.tar.xz"

    def resolve(self, spec: dict) -> dict:
        resolved = prefetch.resolve_redirect(self._url(spec))
        locked = {
            "type": self.type,
            "channel": spec["channel"],
            "resolvedUrl": resolved,
        }
        # .../nixos/unstable/nixos-26.11pre1070770.8ce4ef6cb6f8/nixexprs.tar.xz
        parts = resolved.rstrip("/").split("/")
        if len(parts) >= 2:
            locked["version"] = parts[-2]
            tail = locked["version"].rsplit(".", 1)[-1]
            if re.fullmatch(r"[0-9a-f]{7,40}", tail):
                locked["rev"] = tail
        return locked

    def prefetch(self, locked: dict) -> dict:
        sri, mtime = prefetch.tarball(locked["resolvedUrl"])
        out: dict = {"hash": sri}
        if mtime is not None:
            out["lastModified"] = mtime
        return out

    def fetch_spec(self, locked: dict) -> dict:
        return {"kind": "tarball", "url": locked["resolvedUrl"],
                "hash": locked["hash"]}
