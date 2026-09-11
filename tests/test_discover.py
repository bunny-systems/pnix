from pathlib import Path

from pnix import discover


def _tree(root: Path):
    (root / "a").mkdir(parents=True)
    (root / "a" / "has.nix").write_text('{ pins.foo.type = "github"; }\n')
    (root / "a" / "hasnt.nix").write_text("{ services.nginx.enable = true; }\n")
    (root / "a" / "substring.nix").write_text("{ spins = 1; unpinsed = 2; }\n")
    (root / "notnix.txt").write_text("pins\n")
    return root


def test_finds_only_files_mentioning_the_attr(tmp_path):
    _tree(tmp_path)
    found = {p.name for p in discover.candidates([tmp_path])}
    assert found == {"has.nix"}


def test_word_boundary_excludes_substrings(tmp_path):
    _tree(tmp_path)
    assert not any(p.name == "substring.nix"
                   for p in discover.candidates([tmp_path]))


def test_attr_name_is_configurable(tmp_path):
    (tmp_path / "x.nix").write_text("{ srcs.foo = 1; }\n")
    assert [p.name for p in discover.candidates([tmp_path], attr="srcs")] == ["x.nix"]


def test_results_are_sorted_and_absolute(tmp_path):
    _tree(tmp_path)
    (tmp_path / "b.nix").write_text('{ pins.z = 1; }\n')
    out = discover.candidates([tmp_path])
    assert out == sorted(out)
    assert all(p.is_absolute() for p in out)


def test_overlapping_roots_do_not_duplicate(tmp_path):
    _tree(tmp_path)
    out = discover.candidates([tmp_path, tmp_path / "a"])
    assert len(out) == len(set(out)) == 1


def test_the_vendored_resolver_is_not_a_candidate(tmp_path):
    """`pnix init` writes files that are all about pins and take arguments the
    collector would stub. Without this, init breaks the next update."""
    from pnix import vendor

    vendor.install(tmp_path)
    (tmp_path / "decl.nix").write_text('{ pins.foo.type = "github"; }\n')
    assert [p.name for p in discover.candidates([tmp_path])] == ["decl.nix"]


def test_an_adopted_resolver_file_is_scanned_again(tmp_path):
    """Deleting the marker means the user owns the file; pnix stops making
    assumptions about it, in both directions."""
    from pnix import vendor

    vendor.install(tmp_path)
    adopted = tmp_path / "nix" / "pins" / "resolve.nix"
    adopted.write_text('{ pins.mine.type = "git"; }\n')
    assert adopted.resolve() in discover.candidates([tmp_path])


def test_dot_git_is_skipped(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "junk.nix").write_text('{ pins.x = 1; }\n')
    assert discover.candidates([tmp_path]) == []


def test_a_mention_that_is_not_a_declaration_is_not_a_candidate(tmp_path):
    """`pins` as a dependency name is the nixpkgs case: the Python package
    `pins` is depended on by many package files, all of which matched a plain
    word-boundary grep."""
    (tmp_path / "package.nix").write_text(
        '{ python3Packages }: python3Packages.buildPythonApplication {\n'
        '  dependencies = [ python3Packages.pins ];\n}\n')
    assert discover.candidates([tmp_path]) == []


def test_a_path_containing_the_attr_name_is_not_a_candidate(tmp_path):
    """The consumer's own entry point says `import ./nix/pins`. Importing it
    evaluates the resolver, which reads a lock that does not exist yet on the
    first `pnix update` -- a bootstrap failure, not just noise."""
    (tmp_path / "default.nix").write_text(
        'import ./nix/pins { allFollow = { nixpkgs = "nixpkgs"; }; }\n')
    assert discover.candidates([tmp_path]) == []


def test_both_declaration_shapes_are_found(tmp_path):
    (tmp_path / "dotted.nix").write_text('{ pins.foo.type = "github"; }\n')
    (tmp_path / "block.nix").write_text('{ pins = { foo.type = "git"; }; }\n')
    (tmp_path / "spaced.nix").write_text('{ pins\n  = { foo.type = "git"; }; }\n')
    found = {p.name for p in discover.candidates([tmp_path])}
    assert found == {"dotted.nix", "block.nix", "spaced.nix"}


def test_a_root_that_is_a_file_is_used_directly(tmp_path):
    """`--root ./pins.nix` must not silently yield an empty lock."""
    f = tmp_path / "pins.nix"
    f.write_text('{ pins.foo.type = "github"; }\n')
    assert discover.candidates([f]) == [f.resolve()]
