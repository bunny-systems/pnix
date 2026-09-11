"""Copy the eval-time resolver into a consumer's repository.

The lock is committed, so the code that reads it must be too: a fresh clone
has to build with Nix alone, without pnix installed. tack and npins vendor
their resolvers for exactly this reason.

Only the eval-time half is copied. collect.nix and walk.nix run during
`pnix update`, so they ship with the CLI and cannot drift from it.
"""

import shutil
from pathlib import Path

MARKER = "# pnix-managed. delete this line to take ownership; pnix will leave it alone."

SOURCE = Path(__file__).resolve().parent / "resolver"
DEST = Path("nix") / "pins"

# .nix files get the marker prepended; anything else is copied byte for byte.
MARKABLE = ".nix"
VERBATIM = (".LICENSE", ".md")


class VendorError(Exception):
    pass


def _marked(text: str) -> str:
    return text if text.startswith(MARKER) else f"{MARKER}\n{text}"


def install(project: Path, force: bool = False) -> list[Path]:
    """Write the vendored resolver under <project>/nix/pins, return the paths.

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
            dst.write_text(_marked(src.read_text()))
        written.append(dst)

    if not written:
        raise VendorError(f"no resolver files found under {SOURCE}")
    return written
