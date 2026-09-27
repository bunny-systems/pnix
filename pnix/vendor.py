"""Copy the eval-time resolver into a consumer's repository.

The lock is committed, so the code that reads it must be too: a fresh clone
has to build with Nix alone, without pnix installed. tack and npins vendor
their resolvers for exactly this reason.

Only the eval-time half is copied. collect.nix and walk.nix run during
`pnix update`, so they ship with the CLI and cannot drift from it.

Comments are stripped on the way out. The vendored copy is generated code that
lands in someone else's repository and shows up in their diffs; the reasoning
belongs with the source, which is where anyone changing it will be. Stripping
is whole-line only and provably semantics-preserving -- see
`test_stripping_preserves_the_parse_tree`.
"""

from pathlib import Path

MARKER = "# pnix-managed. delete this line to take ownership; pnix will leave it alone."

#: Stamped on every vendored file, directly under the marker.
#:
#: `pnix init` copies this code into someone else's repository, which is
#: redistribution -- so each file has to say what it is licensed under, and the
#: comment stripper has to leave it alone. tack does the same, and it is the
#: reason a consumer can tell at a glance what landed in their tree.
SPDX = "# SPDX-License-Identifier: EUPL-1.2"

#: The two lines every vendored file opens with.
HEADER = f"{MARKER}\n{SPDX}"

SOURCE = Path(__file__).resolve().parent / "resolver"
DEST = Path(".pnix")

# .nix files get the marker prepended; anything else is copied byte for byte.
MARKABLE = ".nix"
VERBATIM = (".LICENSE", ".md")


class VendorError(Exception):
    pass


def _stripped(text: str) -> str:
    """Drop whole-line comments and collapse the blank runs they leave behind.

    Whole-line only, so no lexer is needed: a `#` that opens a line cannot be
    inside a string unless the string is a `''` block, and a file containing one
    is left alone rather than guessed at. Trailing comments survive, which is
    fine -- the resolver has none, and a wrong strip is far worse than a missed
    one.
    """
    if "''" in text:
        return text

    out: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if not line.strip() and (not out or not out[-1].strip()):
            continue
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n"


def _marked(text: str) -> str:
    """Prepend marker and licence, after stripping, so neither is stripped."""
    return text if text.startswith(HEADER) else f"{HEADER}\n{text}"


def _vendorable() -> list[Path]:
    """Every source file `install` would copy, in a stable order."""
    return [
        p
        for p in sorted(SOURCE.rglob("*"))
        if not p.is_dir() and p.suffix in (MARKABLE, *VERBATIM)
    ]


def _rendered(src: Path) -> bytes:
    """Exactly what `install` writes for this source file."""
    if src.suffix in VERBATIM:
        return src.read_bytes()
    return _marked(_stripped(src.read_text())).encode()


def install(project: Path, force: bool = False) -> list[Path]:
    """Write the vendored resolver under <project>/.pnix, return the paths.

    A destination file that no longer carries MARKER is treated as adopted by
    the user: refuse rather than discard their edits, unless forced.
    """
    root = Path(project) / DEST
    written: list[Path] = []

    for src in _vendorable():
        dst = root / src.relative_to(SOURCE)

        if dst.exists() and not force and MARKER not in dst.read_text():
            raise VendorError(
                f"{dst} has no pnix marker -- it looks adopted. "
                f"Use --force to overwrite it."
            )

        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(_rendered(src))
        written.append(dst)

    if not written:
        raise VendorError(f"no resolver files found under {SOURCE}")
    return written


def stale(project: Path) -> list[Path]:
    """Vendored files that differ from what this pnix would write.

    The lock has `schema` to catch a lock the resolver cannot read. Nothing
    caught the other direction: `.pnix/` changes only when someone runs `pnix
    init`, so a resolver fix sits in the released tool while every consumer
    keeps evaluating the copy they vendored months ago, with no sign that a
    newer one exists.

    Compares content rather than a version stamp. A stamp would have to be
    bumped by hand and would report a difference on every release whether the
    resolver changed or not; the files themselves are the truth, and comparing
    them costs a few reads.

    A file whose MARKER is gone has been adopted deliberately -- `install`
    already refuses to overwrite it, so it is not reported here either.
    """
    root = Path(project) / DEST
    out: list[Path] = []

    for src in _vendorable():
        dst = root / src.relative_to(SOURCE)
        if not dst.exists():
            out.append(dst)
            continue
        current = dst.read_bytes()
        if src.suffix == MARKABLE and MARKER.encode() not in current:
            continue
        if current != _rendered(src):
            out.append(dst)
    return out
