"""The source-type extension point.

Two things a Source owes the resolver:

* `resolve` / `prefetch` -- declaration -> locked node -> hash, at lock time.
* `fetch_spec` -- the closed `{ kind; ... }` discriminator the **vendored**
  resolver reads at eval time.

`fetch.kind` is the contract, not `type`. A source type is free to invent
provenance fields, and adding a forge is a pure Python change, because every
forge reduces to a primitive that already exists in pnix/resolver/fetchers/.
`kinds` declares which primitives a source can emit, and
tests/test_nix.py::test_every_source_kind_has_a_fetcher enforces that they do.
"""

from typing import Protocol


class UnknownSource(Exception):
    pass


class Source(Protocol):
    type: str
    #: fetch primitives this source can emit
    kinds: tuple[str, ...]

    def resolve(self, spec: dict) -> dict:
        """Declaration -> locked node. No hash, no download."""
        ...

    def prefetch(self, locked: dict) -> dict:
        """Locked node -> fields to merge into it. Downloads.

        Returns `{}` for a source that needs nothing added (a git rev is
        already a commitment to the tree). Otherwise typically `hash`, plus
        whatever the download reveals for free -- `lastModified` and
        `lastModifiedDate` for an archive.
        """
        ...

    def fetch_spec(self, locked: dict) -> dict:
        """Locked node -> the `fetch` discriminator the resolver reads."""
        ...
