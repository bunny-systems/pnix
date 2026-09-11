{ }:
let
  fl = import ../../pnix/resolver/eval/flake.nix;
  dir = ./fixtures/flakes;
  throws = v: !(builtins.tryEval (builtins.deepSeq v true)).success;
in
[
  { name = "a directory with flake.nix is a flake";
    expr = fl.isFlake { outPath = dir + "/simple"; };
    expected = true; }

  { name = "a directory without flake.nix is not";
    expr = fl.isFlake { outPath = dir + "/notaflake"; };
    expected = false; }

  { name = "flake = false wins over the file being present";
    expr = fl.isFlake { outPath = dir + "/simple"; flake = false; };
    expected = false; }

  { name = "a nonexistent path does not throw";
    expr = fl.isFlake { outPath = dir + "/does-not-exist"; };
    expected = false; }

  { name = "a sourceInfo with no outPath does not throw";
    expr = fl.isFlake { rev = "abc"; };
    expected = false; }

  { name = "declaredInputs reads every declared name";
    expr = builtins.attrNames (fl.declaredInputs (dir + "/simple"));
    expected = [ "helper" "nested" "nixpkgs" ]; }

  { name = "declaredInputs of a flake with no inputs block is empty";
    expr = fl.declaredInputs (dir + "/indirect");
    expected = { }; }

  { name = "classify: a bare follows";
    expr = fl.classify { follows = "nixpkgs"; };
    expected = "follows"; }

  { name = "classify: url plus sub-input overrides is a spec";
    expr = fl.classify { url = "github:o/r"; inputs.nixpkgs.follows = "nixpkgs"; };
    expected = "spec"; }

  { name = "classify: an already-resolved value is direct";
    expr = fl.classify { outPath = "/nix/store/x"; lib = { }; };
    expected = "direct"; }

  { name = "classify: an empty follows is still a follows";
    expr = fl.classify { follows = ""; };
    expected = "follows"; }

  { name = "classify: an evaluated flake is direct, not a spec";
    expr = fl.classify { _type = "flake"; inputs = { }; outPath = "/nix/store/y"; };
    expected = "direct"; }

  { name = "classify: a non-attrset is direct";
    expr = fl.classify "/nix/store/z";
    expected = "direct"; }

  { name = "calls outputs and returns them";
    expr = (fl.callFlake {
      sourceInfo = { outPath = dir + "/simple"; };
      inputs = { nixpkgs = { marker = "NP"; }; helper = { }; nested = { }; };
    }).value;
    expected = "built"; }

  { name = "self exposes outputs at the top level";
    expr = (fl.callFlake {
      sourceInfo = { outPath = dir + "/selfref"; }; inputs = { };
    }).viaSelf;
    expected = "A"; }

  { name = "an input outputs asks for but never declares is still supplied";
    expr = (fl.callFlake {
      sourceInfo = { outPath = dir + "/indirect"; };
      inputs = { nixpkgs = { marker = "NP"; }; };
    }).fromInput;
    expected = "NP"; }

  { name = "a missing indirect input does not abort the call";
    expr = builtins.isAttrs (fl.callFlake {
      sourceInfo = { outPath = dir + "/indirect"; }; inputs = { };
    });
    expected = true; }

  { name = "but reading that missing input throws by name";
    expr = throws (fl.callFlake {
      sourceInfo = { outPath = dir + "/indirect"; }; inputs = { };
    }).fromInput;
    expected = true; }

  { name = "the result is marked as a flake";
    expr = (fl.callFlake {
      sourceInfo = { outPath = dir + "/selfref"; }; inputs = { };
    })._type;
    expected = "flake"; }

  { name = "sourceInfo attributes survive onto the result";
    expr = (fl.callFlake {
      sourceInfo = { outPath = dir + "/selfref"; rev = "abc"; }; inputs = { };
    }).rev;
    expected = "abc"; }

  { name = "self.inputs is what was passed in";
    expr = (fl.callFlake {
      sourceInfo = { outPath = dir + "/simple"; };
      inputs = { nixpkgs = { }; helper = { }; nested = { }; };
    }).inputNames or (builtins.attrNames (fl.callFlake {
      sourceInfo = { outPath = dir + "/simple"; };
      inputs = { nixpkgs = { }; helper = { }; nested = { }; };
    }).inputs);
    expected = [ "helper" "nested" "nixpkgs" ]; }
]
