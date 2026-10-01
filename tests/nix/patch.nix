# Patch application, with a fake patchPkgs so no nixpkgs is fetched.
{ }:
let
  mkApply = import ../../pnix/resolver/eval/patch.nix;
  throws = v: !(builtins.tryEval (builtins.deepSeq v true)).success;

  # `patch.nix` reaches for `.overrideAttrs` on the result, because
  # `applyPatches` will not let a caller set `allowSubstitutes` through its
  # arguments. The fake has to offer one or the fixed-output branch cannot be
  # exercised at all.
  patchPkgs.applyPatches =
    args:
    let
      self = args // {
        _isDrv = true;
        allowSubstitutes = false;
        overrideAttrs = f: self // f self;
      };
    in
    self;

  apply = mkApply {
    inherit patchPkgs;
    fetchPatch = p: "/fake/patch/${p.hash or p.path}";
    system = "x86_64-linux";
  };

  noSystem = mkApply {
    inherit patchPkgs;
    fetchPatch = p: "/fake/patch/${p.hash or p.path}";
    system = null;
  };

  fod = apply {
    name = "d";
    src = "/fake/src";
    node = {
      patches = [
        {
          kind = "pr";
          url = "u";
          hash = "H";
        }
      ];
      patchedHash = "sha256-XVVwVmNhl3JAxgVm4sm8IBGsvgRcmHTkxBWDE+t7CWc=";
    };
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
  {
    name = "a patchedHash makes the derivation fixed-output";
    expr = {
      inherit (fod.outPath) outputHash outputHashAlgo outputHashMode;
    };
    expected = {
      outputHash = "sha256-XVVwVmNhl3JAxgVm4sm8IBGsvgRcmHTkxBWDE+t7CWc=";
      outputHashAlgo = "sha256";
      outputHashMode = "recursive";
    };
  }
  {
    name = "a fixed-output patched tree is substitutable";
    expr = fod.outPath.allowSubstitutes;
    expected = true;
  }
  {
    name = "without a patchedHash the derivation stays input-addressed";
    expr = patched.outPath ? outputHash;
    expected = false;
  }
  {
    name = "patchFlags are unchanged by the fixed-output branch";
    expr = fod.outPath.patchFlags;
    expected = [
      "-p1"
      "-F0"
      "--no-backup-if-mismatch"
    ];
  }
  # One `patchPkgs` serves every patched pin, so a `system` check placed there
  # would refuse a pin that has a hash because some other pin does not.
  {
    name = "with no system, a hashed pin still resolves";
    expr =
      (noSystem {
        name = "e";
        src = "/fake/src";
        node = {
          patches = [
            {
              kind = "pr";
              url = "u";
              hash = "H";
            }
          ];
          patchedHash = "sha256-AAA";
        };
      }).patched;
    expected = true;
  }
  {
    name = "with no system, an unhashed patched pin is refused";
    expr = throws (
      (noSystem {
        name = "f";
        src = "/fake/src";
        node.patches = [
          {
            kind = "pr";
            url = "u";
            hash = "H";
          }
        ];
      }).outPath
    );
    expected = true;
  }
]
