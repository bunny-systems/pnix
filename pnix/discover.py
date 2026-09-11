"""Narrow a source tree to files that might declare pins.

Nix must never be handed a whole tree to import speculatively. Two reasons,
both measured on nixpkgs' pkgs/by-name (22,791 .nix files):

  * it does not scale -- walking is cheap (0.61 s) but importing is not;
  * it is not safe -- `fetchPackages.nix` there is a single-argument function
    (`fetchFromGitHub:`), which `builtins.functionArgs` reports as `{ }`,
    identical to `{ ... }:`. Calling it with `{ }` errors, and `tryEval` does
    not catch that class of failure.

Scanning bytes is cheap and total: 22,791 files in 1.20 s, 21 candidates.
"""

import os
import re
from pathlib import Path

from pnix.vendor import MARKER

# Pruned during the walk, not filtered afterwards: `.git` in a real repo holds
# thousands of objects, and descending into it to throw the results away is the
# expensive half.
SKIP_DIRS = {"result"}


def _skip(name: str) -> bool:
    """Hidden directories are never scanned.

    `.git`, `.direnv` and `.pnix` were listed by name until a real config
    showed why that is the wrong shape: `~/nixconfig/.tack/default.nix` holds a
    literal `pins = fromTOML …`, so it is a candidate by construction, and
    evaluating it throws `undefined variable 'fetchTree'` because pnix runs with
    experimental features off. That is **not survivable** -- the collector's
    probe uses `tryEval`, which catches a `throw` but *not* an undefined
    variable, so there is no way to skip such a file once it is a candidate.
    Collection aborts for the whole tree.

    Every tool that vendors an unevaluatable Nix file puts it in a dot
    directory. Pruning them all is the rule that covers the next one too.
    """
    return name.startswith(".") or name in SKIP_DIRS

SUFFIX = ".nix"


def _pattern(attr: str) -> re.Pattern[bytes]:
    """Match a *declaration* of `attr`, not any mention of the word.

    A declaration can only take two shapes -- `pins.<name> = …` or
    `pins = { … }` -- so the name must be followed by `.` or `=`, and must not
    be preceded by `.`, which is what makes it an attribute *access*.

    `/` is excluded for the same reason as `.`: a declaration is never written
    `foo/pins.bar`, but a *path* to another tool's data is -- `~/nixconfig`'s
    `override.nix` became a candidate solely because it mentions
    `./.tack/pins.toml`, and that file cannot be evaluated at all.

    All three halves earn their keep. Without the trailing `[.=]`, every nixpkgs
    package that depends on the Python package `pins` is a candidate. Without
    the leading exclusions, a path that merely ends in `pins.something` is one.

    The design's invariant survives intact: a false negative is still
    impossible for a literal declaration, because a literal declaration is
    exactly one of the two shapes above, and neither can follow `.` or `/`.
    """
    word = re.escape(attr).encode()
    return re.compile(
        rb"(?<![A-Za-z0-9_./])" + word + rb"[\"']?\s*[.=]",
        re.DOTALL,
    )


def _walk(root: Path):
    """.nix files under `root`, skipping SKIP_DIRS without descending them.

    A root that is itself a file is used directly. Without this, `--root
    ./pins.nix` walks nothing, finds nothing, and writes an empty lock without
    a word of complaint.
    """
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _skip(d)]
        for name in filenames:
            if name.endswith(SUFFIX):
                yield Path(dirpath) / name


def candidates(roots: list[Path], attr: str = "pins") -> list[Path]:
    """Absolute, sorted paths of .nix files whose text mentions `attr`.

    False positives are fine and expected -- the collector reads `.pins` off
    each and a file without it contributes nothing. False negatives are not
    possible for a literal declaration, since the attribute name must appear
    in the source text.

    The vendored resolver is the exception, and it is not a hypothetical one:
    `pnix init` writes files whose entire subject is pins, so every one of them
    matches the grep, and `resolve.nix` is a function of `lockFile` -- the
    collector calls it with a throwing stub and collection aborts with
    PASS1-FORCED-PIN. `pnix init` would break the next `pnix update`. Skipping
    on the marker rather than on a path keeps that true wherever the consumer
    vendored it, and a file the user has adopted (marker deleted) is scanned
    again, which is the right answer: it is theirs now.
    """
    pattern = _pattern(attr)
    raw = attr.encode()
    marker = MARKER.encode()
    hits: set[Path] = set()
    for root in roots:
        for path in _walk(Path(root)):
            try:
                text = path.read_bytes()
            except OSError:
                continue
            if text.startswith(marker):
                continue
            # Plain substring first. It is memchr-backed and cannot miss a
            # word-boundary match, so it is a pure filter -- and it is the
            # difference between scanning nixpkgs in 3.9 s and in 6.8 s.
            # Measured over 44,024 files / 113 MB: the lookaround regex alone
            # costs 3.26 s, the substring 0.09 s, and the two together 0.52 s
            # because only 88 files survive to reach the regex.
            if raw not in text:
                continue
            if pattern.search(text):
                hits.add(path.resolve())
    return sorted(hits)
