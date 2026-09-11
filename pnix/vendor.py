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

import shutil
from pathlib import Path

MARKER = "# pnix-managed. delete this line to take ownership; pnix will leave it alone."

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
    return text if text.startswith(MARKER) else f"{MARKER}\n{text}"


def install(project: Path, force: bool = False) -> list[Path]:
    """Write the vendored resolver under <project>/.pnix, return the paths.

    A destination file that no longer carries MARKER is treated as adopted by
    the user: refuse rather than discard their edits, unless forced.
    """
    root = Path(project) / DEST
    written: list[Path] = []

    for src in sorted(SOURCE.rglob("*")):
        if src.is_dir() or src.suffix not in (MARKABLE, *VERBATIM):
            continue
        rel = src.relative_to(SOURCE)
        dst = root / rel

        if dst.exists() and not force and MARKER not in dst.read_text():
            raise VendorError(
                f"{dst} has no pnix marker -- it looks adopted. "
                f"Use --force to overwrite it."
            )

        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix in VERBATIM:
            shutil.copyfile(src, dst)
        else:
            dst.write_text(_marked(_stripped(src.read_text())))
        written.append(dst)

    if not written:
        raise VendorError(f"no resolver files found under {SOURCE}")
    return written
