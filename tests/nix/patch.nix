# Patch application, with a fake patchPkgs so no nixpkgs is fetched.
{ }:
let
  mkApply = import ../../pnix/resolver/eval/patch.nix;

  patchPkgs.applyPatches = args: {
    _isDrv = true;
    inherit (args) name patches patchFlags;
    src = args.src;
  };

  apply = mkApply {
    inherit patchPkgs;
    fetchPatch = p: "/fake/patch/${p.hash or p.path}";
  };

  unpatched = apply {
    name = "a";
    src = "/fake/src";
    node = { };
  };
  patched = apply {
    name = "b";
    src = "/fake/src";
    node.patches = [
      {
        kind = "pr";
        url = "u";
        hash = "H";
      }
    ];
  };
  importable = apply {
    name = "c";
    src = "/fake/src";
    node = {
      patches = [
        {
          kind = "pr";
          url = "u";
          hash = "H";
        }
      ];
      importable = true;
    };
  };
in
[
  {
    name = "no patches means the source is passed straight through";
    expr = unpatched.outPath;
    expected = "/fake/src";
  }
  {
    name = "and it is not marked as patched";
    expr = unpatched.patched;
    expected = false;
  }
  {
    name = "patches produce a derivation, not the source";
    expr = patched.outPath._isDrv;
    expected = true;
  }
  {
    name = "the unpatched source stays reachable";
    expr = patched.unpatched;
    expected = "/fake/src";
  }
  {
    name = "fuzz is forbidden, so a mismatched patch fails the build";
    expr = patched.outPath.patchFlags;
    expected = [
      "-p1"
      "-F0"
      "--no-backup-if-mismatch"
    ];
  }
  {
    name = "a patch is fetched by its hash";
    expr = builtins.head patched.outPath.patches;
    expected = "/fake/patch/H";
  }
  {
    name = "a local patch is fetched by its path instead";
    expr =
      builtins.head
        (apply {
          name = "d";
          src = "/fake/src";
          node.patches = [
            {
              kind = "path";
              path = "patches/fix.diff";
            }
          ];
        }).outPath.patches;
    expected = "/fake/patch/patches/fix.diff";
  }
  {
    name = "case A is the default: not importable";
    expr = patched.importable;
    expected = false;
  }
  {
    name = "case B is opt-in";
    expr = importable.importable;
    expected = true;
  }
]
