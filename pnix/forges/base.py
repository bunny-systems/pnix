"""Forge metadata: the only part of pnix that needs a web API.

Ref resolution deliberately does not -- `git ls-remote` covers branches, tags
and revs on every forge with no token. But a pull request is not a git ref, so
tracking one to its head and base commits, and knowing whether it merged, has
to go through the forge.

Each forge answers two questions:

* `pull(owner, repo, number)` -> head, base, state, merged
* `diff_url(...)` -> where to fetch the patch text

**The two verified forges disagree about the second**, and the difference is
load-bearing rather than cosmetic (measured 2026-09-11):

* GitHub serves `compare/BASE...HEAD.diff`, which is **immutable** -- it names
  two commits, so the bytes cannot change when the PR gains commits.
* Forgejo/Gitea serves no compare diff at all (404 on every spelling tried).
  Only `/api/v1/repos/O/R/pulls/N.diff` exists, which is **PR-scoped and
  therefore mutable**.

So a Forgejo patch is pinned by its stored hash rather than by its URL: if the
PR moves, the fetch fails loudly instead of silently changing, and `pnix look`
will already have said the head moved. That is worth knowing before choosing
where to host something you intend to patch.

Do not add a forge here without probing it. The design carried a GitLab row for
weeks that was never verified, and the Forgejo row it listed alongside turned
out to be wrong about exactly this.
"""

from dataclasses import dataclass
from typing import Protocol


class UnknownForge(Exception):
    pass


class ForgeError(Exception):
    pass


@dataclass(frozen=True)
class Pull:
    """What a forge knows about a pull/merge request."""

    head: str
    base: str
    state: str
    merged: bool


class Forge(Protocol):
    name: str
    #: True when diff_url names two commits and so cannot change under us.
    immutable_diffs: bool

    def pull(self, host: str, owner: str, repo: str, number: int) -> Pull:
        """PR metadata. One HTTP request."""
        ...

    def pull_diff_url(self, host: str, owner: str, repo: str, number: int,
                      pull: Pull) -> str:
        """Where to fetch this PR's diff."""
        ...

    def commit_diff_url(self, host: str, owner: str, repo: str, rev: str) -> str:
        """Where to fetch one commit's diff. Always immutable."""
        ...

    def merge_base(self, host: str, owner: str, repo: str,
                   base: str, head: str) -> str | None:
        """The commit a base...head diff is actually generated against.

        Not the same as the PR's `base`, and that difference is the single
        sharpest edge in this feature. Measured on finit PR #181: base is
        `64e41e06`, but the merge base is `c8d6ad65` -- the PR branch is
        "diverged, ahead 1, behind 8". Applying that diff to the base fuzzes
        two hunks through and fails one outright.

        None when the forge has no endpoint pnix has verified for this.
        """
        ...
