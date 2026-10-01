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
  # Both halves of the override case: the pin carries patches *and* a recorded
  # hash, which is the combination that used to produce a guaranteed
  # `hash mismatch in fixed-output derivation`.
  overridden = apply {
    name = "o";
    src = "/my/checkout";
    overridden = true;
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
  # An override replaces the resolved input, so its patches are skipped: the
  # recorded hash describes a tree that was never built, and recomputing it
  # here would mean building during evaluation.
  {
    name = "an overridden pin is handed back as its own tree";
    expr = overridden.outPath;
    expected = "/my/checkout";
  }
  {
    name = "an overridden pin claims no fixed-output hash";
    # The discriminating form. `?` cannot ask this of a path, and that is the
    # point: with the patches applied, `outPath` would be a derivation carrying
    # `outputHash` from the lock.
    expr = builtins.isString overridden.outPath;
    expected = true;
  }
  {
    name = "and it is not marked patched, so it may be probed for a flake";
    expr = overridden.patched;
    expected = false;
  }
  {
    name = "overriding a pin that declares no patches changes nothing";
    expr =
      (apply {
        name = "p";
        src = "/my/checkout";
        overridden = true;
        node = { };
      }).outPath;
    expected = "/my/checkout";
  }
  {
    name = "a pin that is not overridden still gets the fixed-output branch";
    # `overridden` defaults to false, so forgetting to pass it must not quietly
    # disable patching for every pin. `isAttrs` first so a flipped default
    # fails the case rather than erroring on a selection from a path, which
    # reads as a broken test file instead of a broken implementation.
    expr = builtins.isAttrs fod.outPath && fod.outPath._isDrv;
    expected = true;
  }
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
