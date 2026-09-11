"""A literal path on this machine.

Carries no hash, because there is nothing to hash against: the directory can
change under the lock at any moment. That makes it the one source type that
breaks a clean clone elsewhere, so it belongs in a gitignored overrides file or
in PNIX_OVERRIDE, never in a committed declaration. `pnix update` warns when it
finds one.
"""


class LocalPath:
    type = "path"
    kinds = ("path",)

    def resolve(self, spec: dict) -> dict:
        return {"type": self.type, "path": spec["path"]}

    def prefetch(self, locked: dict) -> dict:
        return {}

    def fetch_spec(self, locked: dict) -> dict:
        return {"kind": "path", "path": locked["path"]}
