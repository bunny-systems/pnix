"""Just enough semver to pick the newest tag matching a range.

Deliberately not a general semver implementation. It answers one question --
*which of these tags is the newest one this range accepts* -- and everything it
does is in service of that.

Two rules worth stating because they are where a naive version goes wrong:

* **A prerelease is never matched by a range that does not mention one.**
  `^1.2` must not select `1.3.0-rc1`. This is standard semver behaviour and the
  reason a pin tracking releases does not silently jump onto a release
  candidate.
* **Ordering is by component, not by string.** `1.10.0` is newer than `1.9.0`,
  which string comparison gets backwards.
"""

import re

#: v1.2.3, 1.2.3, 1.2, 1, with optional -prerelease and +build
_VERSION = re.compile(
    r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)

OPERATORS = ("^", "~", ">=", "<=", ">", "<", "=")


class Version:
    __slots__ = ("major", "minor", "patch", "pre", "raw")

    def __init__(self, major, minor, patch, pre, raw):
        self.major, self.minor, self.patch = major, minor, patch
        self.pre, self.raw = pre, raw

    @property
    def key(self) -> tuple:
        # A prerelease sorts *before* its release: 1.2.3-rc1 < 1.2.3.
        return (self.major, self.minor, self.patch, 0 if self.pre else 1,
                _pre_key(self.pre))

    def __repr__(self) -> str:
        return f"Version({self.raw!r})"


def _pre_key(pre: str | None) -> tuple:
    if not pre:
        return ()
    out: list[tuple] = []
    for part in pre.split("."):
        # Numeric identifiers compare numerically and rank below alphanumerics.
        out.append((0, int(part), "") if part.isdigit() else (1, 0, part))
    return tuple(out)


def parse(text: str) -> Version | None:
    """A tag name -> a Version, or None when it is not one.

    Returning None rather than raising is deliberate: a repository's tag list is
    a mixed bag -- `nixos-24.05`, `latest`, `debian/1.2-3` -- and a range query
    should ignore what it cannot read rather than fail on it.
    """
    m = _VERSION.match(text.strip())
    if not m:
        return None
    major, minor, patch, pre = m.groups()
    return Version(int(major), int(minor or 0), int(patch or 0), pre, text)


def _bump(v: Version, caret: bool) -> tuple:
    """The exclusive upper bound of a `^` or `~` range."""
    if caret:
        # ^0.2.3 is >=0.2.3 <0.3.0: before 1.0.0 the minor is the breaking one.
        if v.major:
            return (v.major + 1, 0, 0)
        if v.minor:
            return (v.major, v.minor + 1, 0)
        return (v.major, v.minor, v.patch + 1)
    return (v.major, v.minor + 1, 0)


def satisfies(v: Version, spec: str) -> bool:
    spec = spec.strip()
    if spec in ("*", "", "latest", "any"):
        return v.pre is None

    op = next((o for o in OPERATORS if spec.startswith(o)), "")
    bound = parse(spec[len(op):]) if op else parse(spec)
    if bound is None:
        return False

    # A range without a prerelease never selects one.
    if v.pre and not bound.pre:
        return False

    lo = (v.major, v.minor, v.patch)
    at = (bound.major, bound.minor, bound.patch)

    if op in ("^", "~"):
        return at <= lo < _bump(bound, op == "^")
    if op == ">=":
        return v.key >= bound.key
    if op == ">":
        return v.key > bound.key
    if op == "<=":
        return v.key <= bound.key
    if op == "<":
        return v.key < bound.key
    return v.key == bound.key          # bare or "=": exact


def newest(names, spec: str) -> str | None:
    """The newest tag name satisfying `spec`, or None."""
    matches = [
        (v.key, name)
        for name in names
        if (v := parse(name)) is not None and satisfies(v, spec)
    ]
    return max(matches)[1] if matches else None
