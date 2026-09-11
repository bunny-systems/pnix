import pytest

from pnix import collect

DEMO = """
{ inputs, ... }: { pins.demo = { type = "github"; owner = "o"; repo = "r"; }; }
"""


def test_collect_returns_pins_and_provenance(tmp_path):
    demo = tmp_path / "demo.nix"
    demo.write_text(DEMO)
    pins, prov, _ = collect.collect([demo])
    assert pins["demo"]["owner"] == "o"
    assert prov["demo"].endswith("demo.nix")


def test_collect_honours_a_custom_attr(tmp_path):
    f = tmp_path / "x.nix"
    f.write_text('{ srcs.foo = { type = "git"; url = "u"; }; }\n')
    pins, _, _ = collect.collect([f], attr="srcs")
    assert pins["foo"]["url"] == "u"


def test_collect_with_no_files_returns_empty():
    assert collect.collect([]) == ({}, {}, [])


def test_a_candidate_that_cannot_be_forced_is_skipped_not_fatal(tmp_path):
    """The nixpkgs case, in miniature.

    Measured over all 44,024 .nix files in nixpkgs: the grep yields 56
    candidates and 49 of them are package expressions whose weak head normal
    form is a builder call. Aborting on those means pnix cannot be pointed at
    a large tree at all.
    """
    pkg = tmp_path / "package.nix"
    pkg.write_text(
        '{ lib, python3Packages }: python3Packages.buildPythonApplication {\n'
        '  pname = "thing"; dependencies = [ python3Packages.pins ];\n'
        '}\n')
    real = tmp_path / "decl.nix"
    real.write_text('{ pins.foo = { type = "git"; url = "u"; }; }\n')

    pins, _, skipped = collect.collect([pkg, real])
    assert set(pins) == {"foo"}
    assert skipped == [str(pkg)]


def test_a_forced_pin_value_is_still_fatal(tmp_path):
    """The probe must not swallow this: WHNF succeeds here, and the throw is
    in the pin's value, which the caller forces with --strict."""
    bad = tmp_path / "bad.nix"
    bad.write_text(
        '{ inputs, ... }: { pins.p = { type = "github"; rev = inputs.o.rev; }; }\n')
    with pytest.raises(collect.CollectError) as e:
        collect.collect([bad])
    assert "PASS1-FORCED-PIN" in str(e.value)




def test_a_hard_failure_names_the_file_that_caused_it(tmp_path):
    """The WHNF probe catches `throw`, not IO errors: `import ./nope` aborts
    the whole run, and Nix's trace names collect.nix rather than the file."""
    good = tmp_path / "decl.nix"
    good.write_text('{ pins.foo = { type = "git"; url = "u"; }; }\n')
    bad = tmp_path / "imports-nothing.nix"
    bad.write_text('{ pins = import ./does-not-exist; }\n')

    with pytest.raises(collect.CollectError) as e:
        collect.collect([good, bad])
    msg = str(e.value)
    assert "imports-nothing.nix" in msg
    assert "decl.nix" not in msg.split("could not be evaluated")[-1]


def test_declarations_scattered_across_a_tree_merge_into_one_set(tmp_path):
    """The design's central claim: a declaration lives in the file that
    consumes it. Verified against a real config -- 23 pins across 15 sites,
    inline in module files, in sibling data files, and in a directory holding
    nothing else -- all producing one lock.
    """
    from pnix import discover

    (tmp_path / "a" / "deep").mkdir(parents=True)
    (tmp_path / "b").mkdir()
    # inline in a module, beside its consumer, with stubbed arguments
    (tmp_path / "a" / "mod.nix").write_text(
        '{ config, inputs, ... }: {\n'
        '  pins.alpha = { type = "git"; url = "a"; };\n'
        '  services.thing.package = inputs.alpha;\n}\n')
    # a sibling data file, which is not a module at all
    (tmp_path / "a" / "deep" / "pins.nix").write_text(
        '{ pins.beta = { type = "git"; url = "b"; }; }\n')
    # a directory whose only content is a declaration
    (tmp_path / "b" / "only.nix").write_text(
        '{ pins.gamma = { type = "git"; url = "c"; }; }\n')

    pins, prov, skipped = collect.collect(discover.candidates([tmp_path]))
    assert set(pins) == {"alpha", "beta", "gamma"}
    assert skipped == []
    assert prov["alpha"].endswith("mod.nix")
    assert prov["gamma"].endswith("only.nix")


def test_the_same_pin_declared_identically_twice_merges(tmp_path):
    for name in ("one.nix", "two.nix"):
        (tmp_path / name).write_text(
            '{ pins.shared = { type = "git"; url = "u"; }; }\n')
    pins, _, _ = collect.collect(sorted(tmp_path.glob("*.nix")))
    assert pins["shared"]["url"] == "u"


def test_the_same_pin_declared_differently_names_both_files(tmp_path):
    (tmp_path / "one.nix").write_text('{ pins.s = { type = "git"; url = "a"; }; }\n')
    (tmp_path / "two.nix").write_text('{ pins.s = { type = "git"; url = "b"; }; }\n')
    with pytest.raises(collect.CollectError) as e:
        collect.collect(sorted(tmp_path.glob("*.nix")))
    msg = str(e.value)
    assert "one.nix" in msg and "two.nix" in msg
