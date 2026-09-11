import pytest

from pnix import cli, vendor


def test_install_writes_the_eval_time_files(tmp_path):
    written = vendor.install(tmp_path)
    names = {p.name for p in written}
    assert {"resolve.nix", "flake.nix", "follows.nix", "upstream.nix"} <= names
    assert (tmp_path / ".pnix" / "fetchers" / "tarball.nix").exists()
    assert (tmp_path / ".pnix" / "fetchers" / "git.nix").exists()


def test_fetchers_are_primitives_not_source_types(tmp_path):
    """Adding a forge must not put a new file in the consumer's repo."""
    vendor.install(tmp_path)
    have = {p.stem for p in (tmp_path / ".pnix" / "fetchers").glob("*.nix")}
    assert have == {"default", "tarball", "file", "git", "path"}


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
    target = tmp_path / ".pnix" / "resolve.nix"
    assert vendor.MARKER in target.read_text()


def test_refuses_to_clobber_a_file_that_lost_its_marker(tmp_path):
    vendor.install(tmp_path)
    target = tmp_path / ".pnix" / "resolve.nix"
    target.write_text("# mine now\n")
    with pytest.raises(vendor.VendorError) as e:
        vendor.install(tmp_path)
    assert "resolve.nix" in str(e.value)
    assert target.read_text() == "# mine now\n"


def test_force_overwrites_anyway(tmp_path):
    vendor.install(tmp_path)
    target = tmp_path / ".pnix" / "resolve.nix"
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
