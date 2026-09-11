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


def test_the_vendored_directory_is_skipped_by_location(tmp_path):
    """`.pnix/` is resolver machinery whatever its contents, so it is pruned by
    name. Before the move the rule was the marker comment alone, which meant
    adopting a vendored file put it back in the scan -- and a resolver file is
    never a declaration site, so that was the wrong direction to fail in."""
    from pnix import vendor

    vendor.install(tmp_path)
    adopted = tmp_path / ".pnix" / "eval" / "resolve.nix"
    adopted.write_text('{ pins.mine.type = "git"; }\n')
    assert discover.candidates([tmp_path]) == []


def test_a_marked_resolver_file_outside_the_dot_directory_is_still_skipped(tmp_path):
    """The marker rule survives the move: it covers a resolver copied somewhere
    other than `.pnix/`, which `--root` on a parent would otherwise sweep up."""
    from pnix import vendor

    elsewhere = tmp_path / "vendor" / "resolver"
    elsewhere.mkdir(parents=True)
    (elsewhere / "resolve.nix").write_text(
        vendor.MARKER + '\n{ pins.mine.type = "git"; }\n')
    assert discover.candidates([tmp_path]) == []


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
    """A consumer's entry point imports the resolver by path. Importing it
    evaluates the resolver, which reads a lock that does not exist yet on the
    first `pnix update` -- a bootstrap failure, not just noise. `./.pnix` no
    longer contains the attr name at all, but a path segment called `pins` is
    still something a project may legitimately have."""
    (tmp_path / "default.nix").write_text(
        'import ./.pnix { allFollow = { nixpkgs = "nixpkgs"; }; }\n')
    (tmp_path / "legacy.nix").write_text(
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


def test_a_hidden_directory_is_never_scanned(tmp_path):
    """`~/nixconfig/.tack/default.nix` holds a literal `pins = fromTOML …`, so
    it is a candidate by construction, and evaluating it throws `undefined
    variable 'fetchTree'` under `experimental-features ""`. The collector's
    probe cannot rescue that: `tryEval` catches a throw but not an undefined
    variable, so collection aborts for the whole tree. Pruning hidden dirs is
    the only place this can be stopped."""
    tack = tmp_path / ".tack"
    tack.mkdir()
    (tack / "default.nix").write_text("let pins = { }; in pins\n")
    (tmp_path / "real.nix").write_text('{ pins.foo.type = "github"; }\n')
    assert [p.name for p in discover.candidates([tmp_path])] == ["real.nix"]


def test_a_path_ending_in_the_attr_name_is_not_a_candidate(tmp_path):
    """`~/nixconfig/override.nix` matched only because it mentions
    `./.tack/pins.toml`. A declaration is never written `foo/pins.bar`."""
    (tmp_path / "override.nix").write_text(
        'builtins.fromTOML (builtins.readFile ./.tack/pins.toml)\n')
    assert discover.candidates([tmp_path]) == []


def test_an_explicit_hidden_root_is_still_honoured(tmp_path):
    """Pruning applies to the walk, not to what the user names. `--root` is how
    you say you meant it."""
    hidden = tmp_path / ".config"
    hidden.mkdir()
    decl = hidden / "decl.nix"
    decl.write_text('{ pins.foo.type = "github"; }\n')
    assert discover.candidates([decl]) == [decl.resolve()]
