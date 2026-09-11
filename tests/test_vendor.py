import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pnix import cli, vendor

RESOLVER = Path(vendor.SOURCE)


def _eval_json(expr: str):
    out = subprocess.run(
        ["nix-instantiate", "--eval", "--strict", "--json", "--expr", expr,
         "--option", "experimental-features", ""],
        capture_output=True, text=True, env=dict(os.environ), check=False,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _parse(path: Path, root: Path) -> str:
    """Nix's parse tree, with absolute path literals made relative to `root`.

    Path literals resolve against the file's own directory, so the vendored copy
    and the source disagree on every `./x.nix` -- the one difference that is
    expected and meaningless. Normalised against the tree root rather than each
    file's parent, because `resolve.nix` reaches up to `../pins.lock.json`.
    """
    out = subprocess.run(
        ["nix-instantiate", "--parse", str(path),
         "--option", "experimental-features", ""],
        capture_output=True, text=True, env=dict(os.environ), check=False,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.replace(str(root.resolve()), "<DIR>")


def test_install_writes_the_eval_time_files(tmp_path):
    written = vendor.install(tmp_path)
    names = {p.name for p in written}
    assert {"resolve.nix", "flake.nix", "follows.nix", "upstream.nix"} <= names
    assert (tmp_path / ".pnix" / "eval" / "fetchers.nix").exists()


def test_fetchers_are_primitives_not_source_types(tmp_path):
    """Adding a forge must not add a fetcher. Asserted on the evaluated attrset
    rather than on filenames, because the four primitives now share one file --
    a file each implied that a new forge needed a new one, which is the opposite
    of what the dispatch is for."""
    vendor.install(tmp_path)
    have = _eval_json(f"builtins.attrNames (import {tmp_path}/.pnix/eval/fetchers.nix "
                      "{ }).primitives")
    assert set(have) == {"tarball", "file", "git", "path"}


def test_install_does_not_vendor_lock_time_files(tmp_path):
    """collect.nix and walk.nix run only during `pnix update`, so they ship
    with the CLI and can never drift from the code that invokes it."""
    names = {p.name for p in vendor.install(tmp_path)}
    assert "collect.nix" not in names and "walk.nix" not in names


def test_install_does_not_vendor_with_inputs(tmp_path):
    """pnix carries no third-party code. with-inputs is an answer key kept
    outside the repo, never a dependency."""
    names = {p.name for p in vendor.install(tmp_path)}
    assert "with-inputs.nix" not in names


def test_the_vendored_tree_is_importable_on_its_own(tmp_path):
    """A fresh clone builds with Nix alone: .pnix must be self-contained."""
    vendor.install(tmp_path)
    assert (tmp_path / ".pnix" / "default.nix").exists()


def test_written_files_carry_the_marker(tmp_path):
    for path in vendor.install(tmp_path):
        assert vendor.MARKER in path.read_text()


def test_reinstall_is_idempotent(tmp_path):
    vendor.install(tmp_path)
    vendor.install(tmp_path)          # must not raise
    target = tmp_path / ".pnix" / "eval" / "resolve.nix"
    assert vendor.MARKER in target.read_text()


def test_refuses_to_clobber_a_file_that_lost_its_marker(tmp_path):
    vendor.install(tmp_path)
    target = tmp_path / ".pnix" / "eval" / "resolve.nix"
    target.write_text("# mine now\n")
    with pytest.raises(vendor.VendorError) as e:
        vendor.install(tmp_path)
    assert "resolve.nix" in str(e.value)
    assert target.read_text() == "# mine now\n"


def test_force_overwrites_anyway(tmp_path):
    vendor.install(tmp_path)
    target = tmp_path / ".pnix" / "eval" / "resolve.nix"
    target.write_text("# mine now\n")
    vendor.install(tmp_path, force=True)
    assert vendor.MARKER in target.read_text()


def test_the_layout_is_a_single_dot_directory(tmp_path):
    """One directory is the whole of pnix in a consumer repo, so `rm -rf .pnix`
    uninstalls it and nothing squats a namespace the project owns. Asserted on
    the literal, because the path is public: consumers write `import ./.pnix`
    and moving it is a breaking change, not a refactor."""
    written = vendor.install(tmp_path)
    assert all(p.is_relative_to(tmp_path / ".pnix") for p in written)
    assert [p.name for p in tmp_path.iterdir()] == [".pnix"]
    assert str(cli.LOCK_NAME) == ".pnix/pins.lock.json"


def test_vendored_files_carry_no_comments(tmp_path):
    """The vendored copy is generated code in someone else's repository. The
    reasoning stays with the source, where whoever changes it will be."""
    for path in vendor.install(tmp_path):
        body = path.read_text().split("\n", 1)[1]
        assert not [l for l in body.splitlines() if l.lstrip().startswith("#")]


def test_the_marker_survives_stripping(tmp_path):
    """It is itself a comment, and `init` refusing to clobber an adopted file
    depends on it."""
    for path in vendor.install(tmp_path):
        assert path.read_text().startswith(vendor.MARKER)


def test_stripping_preserves_the_parse_tree(tmp_path):
    """Comments do not appear in Nix's AST, so a strip that changed behaviour
    would change the parse. This is what licenses stripping without a lexer."""
    root = tmp_path / ".pnix"
    for dst in vendor.install(tmp_path):
        src = RESOLVER / dst.relative_to(root)
        assert _parse(dst, root) == _parse(src, RESOLVER), dst.name


def test_a_multiline_string_defeats_the_strip(tmp_path, monkeypatch):
    """A line opening with `#` inside a `''` block is text, not a comment.
    Whole-line stripping cannot see the difference, so such a file is left
    whole rather than guessed at."""
    assert vendor._stripped("# gone\nx\n") == "x\n"
    kept = "# kept\ns = \'\'\n# not a comment\n\'\';\n"
    assert vendor._stripped(kept) == kept


def test_the_vendored_copy_is_already_formatted(tmp_path):
    """Otherwise every consumer's `nix fmt` rewrites `.pnix/` and the next
    `pnix init` rewrites it back -- a loop that churns five files in every diff.
    Stripping comments could in principle change what nixfmt wants; this is the
    check that it does not."""
    nixfmt = shutil.which("nixfmt")
    if nixfmt is None:
        pytest.skip("nixfmt not on PATH")
    unformatted = [
        p.name for p in vendor.install(tmp_path)
        if p.suffix == ".nix"
        and subprocess.run([nixfmt, "--check", str(p)], check=False,
                           capture_output=True).returncode != 0
    ]
    assert not unformatted, f"vendored but not nixfmt-clean: {unformatted}"
