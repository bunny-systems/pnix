# Evaluates to a list of { name, expr, expected } assertions.
#
# Cases that expect a throw wrap the value in builtins.deepSeq: tryEval only
# forces to WHNF, so a throw buried in a pin's *value* -- the exact violation
# valdep.nix stands for -- would otherwise go uncaught and the case would pass
# against a collector that never looks at it.
{ }:
let
  collect = import ../../pnix/nixsrc/collect.nix;
  dir = ./fixtures/collect;

  run = fs: collect { files = fs; };
  throws = v: !(builtins.tryEval (builtins.deepSeq v true)).success;
in
[
  {
    name = "reads a pin from a function module";
    expr = (run [ (dir + "/a.nix") ]).pins.foo.owner;
    expected = "o";
  }
  {
    name = "records the declaring file";
    expr = builtins.baseNameOf (run [ (dir + "/a.nix") ]).provenance.foo;
    expected = "a.nix";
  }
  {
    name = "identical declarations in two files merge";
    expr =
      (run [
        (dir + "/a.nix")
        (dir + "/same.nix")
      ]).pins.foo.owner;
    expected = "o";
  }
  {
    name = "differing declarations throw";
    expr =
      throws
        (run [
          (dir + "/a.nix")
          (dir + "/diff.nix")
        ]).pins;
    expected = true;
  }
  {
    name = "an input forced inside top-level imports is irrelevant";
    expr = (run [ (dir + "/forced.nix") ]).pins.baz.type;
    expected = "github";
  }
  {
    name = "a pin value depending on a module arg throws";
    expr = throws (run [ (dir + "/valdep.nix") ]).pins;
    expected = true;
  }
  {
    name = "a plain attrset module works";
    expr = (run [ (dir + "/plain.nix") ]).pins.bare.type;
    expected = "git";
  }
  {
    name = "a file with no pins contributes nothing";
    expr = (run [ (dir + "/nopins.nix") ]).pins;
    expected = { };
  }
  {
    name = "an unforceable package expression is skipped, not fatal";
    expr =
      (run [
        (dir + "/pkglike.nix")
        (dir + "/a.nix")
      ]).pins.foo.owner;
    expected = "o";
  }
  {
    name = "and the skip is reported rather than silent";
    expr =
      map builtins.baseNameOf
        (run [
          (dir + "/pkglike.nix")
          (dir + "/a.nix")
        ]).skipped;
    expected = [ "pkglike.nix" ];
  }
  {
    name = "a file that forces cleanly is never in skipped";
    expr = (run [ (dir + "/a.nix") ]).skipped;
    expected = [ ];
  }
  {
    name = "a path literal is reported as the path it named, not a store path";
    expr =
      let
        p = builtins.head (run [ (dir + "/pathy.nix") ]).pins.withpath.patches;
      in
      builtins.isString p && builtins.match ".*/nix/store/.*" p == null;
    expected = true;
  }
  {
    name = "and it still points at the right file";
    expr = builtins.baseNameOf (builtins.head (run [ (dir + "/pathy.nix") ]).pins.withpath.patches);
    expected = "a.nix";
  }
  {
    name = "walk finds every .nix file recursively";
    expr = builtins.length (import ../../pnix/nixsrc/walk.nix dir);
    expected = 9;
  }
]
