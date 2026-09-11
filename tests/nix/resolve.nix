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
]
