"""Both Nix halves must survive installation.

They live inside the Python package rather than in a repo-root nix/ directory
for exactly this reason, and the paths below are what the CLI resolves at
runtime -- so if package-data ever stops shipping them, this fails rather than
`pnix update` failing on a user's machine.
"""

import tomllib
from pathlib import Path

from pnix import collect, vendor

ROOT = Path(__file__).resolve().parent.parent


def test_the_lock_time_collector_is_where_the_cli_looks():
    assert (collect.NIXSRC / "collect.nix").is_file()
    assert (collect.NIXSRC / "walk.nix").is_file()


def test_the_eval_time_resolver_is_where_vendor_looks():
    assert (vendor.SOURCE / "resolve.nix").is_file()
    assert (vendor.SOURCE / "fetchers.nix").is_file()


def test_package_data_covers_every_shipped_nix_file():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    patterns = cfg["tool"]["setuptools"]["package-data"]["pnix"]
    shipped = {
        p.relative_to(ROOT / "pnix").as_posix()
        for tree in ("nixsrc", "resolver")
        for p in (ROOT / "pnix" / tree).rglob("*.nix")
    }
    covered = {m.as_posix() for pat in patterns
               for m in (ROOT / "pnix").glob(pat)}
    covered = {Path(c).relative_to("pnix").as_posix() if c.startswith("pnix/")
               else c for c in covered}
    missing = {
        f for f in shipped
        if not any((ROOT / "pnix" / f).match(pat) for pat in patterns)
    }
    assert not missing, f"not covered by package-data: {sorted(missing)}"


def test_every_python_subpackage_is_declared():
    """setuptools is told the package list explicitly, so a new subpackage is
    invisible until someone remembers. `pnix.forges` was missing and only the
    derivation build caught it, with an ImportError at pythonImportsCheck."""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    declared = set(cfg["tool"]["setuptools"]["packages"])
    found = {
        f"pnix.{d.name}"
        for d in (ROOT / "pnix").iterdir()
        if d.is_dir() and (d / "__init__.py").is_file()
    } | {"pnix"}
    assert found <= declared, f"undeclared packages: {sorted(found - declared)}"
