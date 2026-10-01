# End-to-end wiring: lock -> fetch -> evaluate -> sub-inputs.
#
# `overrides` stands in for the fetchers so the test needs no network: every
# pin resolves to a fixture directory, and everything downstream of fetching
# is the real code path.
{ }:
let
  resolve = import ../../pnix/resolver/eval/resolve.nix;
  flakes = ./fixtures/flakes;
  throws = v: !(builtins.tryEval (builtins.deepSeq v true)).success;

  inputs = resolve {
    lockFile = ./fixtures/locks/demo.lock.json;
    allFollow = {
      dep = "dep";
    };
    overrides = {
      consumer = flakes + "/consumer";
      dep = flakes + "/dep";
      plain = flakes + "/notaflake";
      sub = flakes + "/mono";
    };
  };
in
[
  {
    name = "every pin in the lock becomes an input";
    expr = builtins.attrNames inputs;
    expected = [
      "consumer"
      "dep"
      "plain"
      "sub"
    ];
  }
  {
    name = "a pinned flake is evaluated";
    expr = inputs.dep.marker;
    expected = "DEP";
  }
  {
    name = "a sub-input resolves through allFollow to our pin";
    expr = inputs.consumer.got;
    expected = "DEP";
  }
  {
    name = "a non-flake pin is source only";
    expr = inputs.plain ? _type;
    expected = false;
  }
  {
    name = "a non-flake pin still carries its rev";
    expr = inputs.plain.rev;
    expected = "3333333333333333333333333333333333333333";
  }
  {
    name = "sourceInfo does not leak onto a flake's outputs namespace";
    expr = inputs.dep._type;
    expected = "flake";
  }
  {
    name = "`dir` finds a flake in a subdirectory";
    expr = inputs.sub.marker;
    expected = "SUB";
  }
  {
    name = "and the flake's outPath is the subdirectory";
    expr = builtins.baseNameOf inputs.sub.outPath;
    expected = "sub";
  }
  {
    name = "while sourceInfo still points at the source root";
    expr = builtins.baseNameOf inputs.sub.sourceInfo.outPath;
    expected = "mono";
  }
  {
    name = "a future lock schema is refused";
    expr = throws (resolve {
      lockFile = ./fixtures/locks/future.lock.json;
    });
    expected = true;
  }

  # Applying a patch is a derivation, so it needs a system, and pure evaluation
  # has none to offer. `system = null` is what the default collapses to there.
  {
    name = "a patched pin with no system to build for is refused";
    expr = throws (
      (resolve {
        lockFile = ./fixtures/locks/patched.lock.json;
        system = null;
        overrides = {
          nixpkgs = flakes + "/notaflake";
          patched = flakes + "/simple";
        };
      }).patched
    );
    expected = true;
  }
  {
    name = "and an unpatched pin alongside it still resolves";
    expr =
      (resolve {
        lockFile = ./fixtures/locks/patched.lock.json;
        system = null;
        overrides = {
          nixpkgs = flakes + "/notaflake";
          patched = flakes + "/simple";
        };
      }).nixpkgs.rev;
    expected = "4444444444444444444444444444444444444444";
  }

  # With a patchedHash the output is content-addressed, so `system` no longer
  # decides the result and pure evaluation stops being a dead end.
  {
    name = "a hashed patched pin resolves with no system to build for";
    expr =
      (resolve {
        lockFile = ./fixtures/locks/patched-hashed.lock.json;
        system = null;
        overrides = {
          nixpkgs = flakes + "/fakepkgs";
          patched = flakes + "/simple";
        };
      }).patched.narHash;
    expected = "sha256-XVVwVmNhl3JAxgVm4sm8IBGsvgRcmHTkxBWDE+t7CWc=";
  }
  {
    name = "an unpatched pin still reports its fetch hash as narHash";
    expr =
      (resolve {
        lockFile = ./fixtures/locks/patched-hashed.lock.json;
        system = null;
        overrides = {
          nixpkgs = flakes + "/fakepkgs";
          patched = flakes + "/simple";
        };
      }).nixpkgs.narHash;
    expected = "sha256-DDD";
  }
  {
    name = "patchedOnly exposes just the patched pins, as derivations";
    expr = builtins.attrNames (resolve {
      lockFile = ./fixtures/locks/patched-hashed.lock.json;
      patchedOnly = true;
      overrides = {
        nixpkgs = flakes + "/fakepkgs";
        patched = flakes + "/simple";
      };
    });
    expected = [ "patched" ];
  }
  # Forces `patchPkgs` itself with no system: the narHash cases above read a
  # value straight from the lock and never build anything, so they cannot tell a
  # working fallback from a throw. This is the pure-eval path the whole change
  # exists for, and `fakepkgs` records the system it was called with.
  {
    name = "with no system, patchPkgs falls back instead of refusing";
    expr =
      (resolve {
        lockFile = ./fixtures/locks/patched-hashed.lock.json;
        system = null;
        patchedOnly = true;
        overrides = {
          nixpkgs = flakes + "/fakepkgs";
          patched = flakes + "/simple";
        };
      }).patched.system;
    expected = "x86_64-linux";
  }
  {
    name = "an explicit system reaches patchPkgs unchanged";
    expr =
      (resolve {
        lockFile = ./fixtures/locks/patched-hashed.lock.json;
        system = "aarch64-linux";
        patchedOnly = true;
        overrides = {
          nixpkgs = flakes + "/fakepkgs";
          patched = flakes + "/simple";
        };
      }).patched.system;
    expected = "aarch64-linux";
  }
  {
    name = "patchedOnly hands back the applyPatches arguments, hash included";
    expr =
      (resolve {
        lockFile = ./fixtures/locks/patched-hashed.lock.json;
        patchedOnly = true;
        overrides = {
          nixpkgs = flakes + "/fakepkgs";
          patched = flakes + "/simple";
        };
      }).patched.outputHash;
    expected = "sha256-XVVwVmNhl3JAxgVm4sm8IBGsvgRcmHTkxBWDE+t7CWc=";
  }
]
