"""github.com and GitHub Enterprise.

Verified 2026-09-11 against finit PR #181: head fa14ed16, base 64e41e06, state
closed, merged true -- and `compare/64e41e06...fa14ed16.diff` is 17374 bytes,
identical to `/pull/181.diff` and to `/commit/fa14ed16.diff` (that PR has one
commit, which is why those three agree).
"""

from pnix.forges import http
from pnix.forges.base import Pull

DEFAULT_HOST = "github.com"


def _api(host: str) -> str:
    return "https://api.github.com" if host == DEFAULT_HOST else f"https://{host}/api/v3"


class GitHub:
    name = "github"
    immutable_diffs = True

    def pull(self, host: str, owner: str, repo: str, number: int) -> Pull:
        doc = http.get_json(f"{_api(host)}/repos/{owner}/{repo}/pulls/{number}")
        return Pull(
            head=doc["head"]["sha"],
            base=doc["base"]["sha"],
            state=doc["state"],
            merged=bool(doc.get("merged")),
        )

    def pull_diff_url(self, host, owner, repo, number, pull) -> str:
        """The compare diff, not `/pull/N.diff`.

        Both render the same bytes today, but only this one names the two
        commits it spans, so it cannot change when the PR gains commits. That
        matters because the lock stores a hash: a mutable URL would turn an
        upstream push into a hash mismatch on someone else's machine.
        """
        return (f"https://{host}/{owner}/{repo}/compare/"
                f"{pull.base}...{pull.head}.diff")

    def commit_diff_url(self, host, owner, repo, rev) -> str:
        return f"https://{host}/{owner}/{repo}/commit/{rev}.diff"

    def merge_base(self, host, owner, repo, base, head) -> str | None:
        doc = http.get_json(
            f"{_api(host)}/repos/{owner}/{repo}/compare/{base}...{head}")
        return doc.get("merge_base_commit", {}).get("sha")
