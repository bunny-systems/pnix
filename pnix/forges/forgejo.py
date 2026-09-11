"""Forgejo and Gitea -- codeberg.org, and forgejo.nimeses.com.

Verified 2026-09-11 against codeberg.org/BANanaD3V/niri-nix PR #32: the API
shape matches GitHub's closely enough to share field names, and
`/api/v1/repos/O/R/pulls/32.diff` returns 15276 bytes.

**There is no compare diff.** `compare/BASE...HEAD.diff`, `compare/BASE..HEAD.diff`
and `.patch` all return 404. So unlike GitHub, the only PR-range diff available
is the PR's own, which moves when the PR does -- hence `immutable_diffs = False`.

That asymmetry is visible in the numbers: PR #32's diff is 15276 bytes while its
head commit's diff is 672, because the PR has several commits. Pinning a
multi-commit Forgejo PR to its head commit -- which the design originally
proposed for every forge -- would silently apply a fraction of the change.
"""

from pnix.forges import http
from pnix.forges.base import Pull

DEFAULT_HOST = "codeberg.org"


class Forgejo:
    name = "forgejo"
    immutable_diffs = False

    def pull(self, host: str, owner: str, repo: str, number: int) -> Pull:
        doc = http.get_json(
            f"https://{host}/api/v1/repos/{owner}/{repo}/pulls/{number}")
        return Pull(
            head=doc["head"]["sha"],
            base=doc["base"]["sha"],
            state=doc["state"],
            merged=bool(doc.get("merged")),
        )

    def pull_diff_url(self, host, owner, repo, number, pull) -> str:
        return f"https://{host}/api/v1/repos/{owner}/{repo}/pulls/{number}.diff"

    def commit_diff_url(self, host, owner, repo, rev) -> str:
        return f"https://{host}/{owner}/{repo}/commit/{rev}.diff"

    def merge_base(self, host, owner, repo, base, head) -> str | None:
        """Not implemented: Forgejo's compare endpoint was not probed.

        Returning None loses the lock-time warning about a diff generated
        against a different tree; the build still fails loudly if the patch
        does not apply, because fuzz is forbidden. Guessing an endpoint shape
        here is what produced the wrong Forgejo row in the design.
        """
        return None
