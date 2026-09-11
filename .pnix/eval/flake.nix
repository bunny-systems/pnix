# pnix-managed. delete this line to take ownership; pnix will leave it alone.
rec {

  specKeys = [
    "url"
    "type"
    "owner"
    "repo"
    "host"
    "ref"
    "rev"
    "dir"
    "flake"
    "submodules"
    "inputs"
    "follows"
  ];

  classify =
    v:
    if !builtins.isAttrs v then
      "direct"
    else if v ? follows then
      "follows"
    else if v ? outPath || v ? _type then
      "direct"
    else if builtins.any (k: v ? ${k}) specKeys then
      "spec"
    else
      "direct";

  isFlake =
    sourceInfo:
    let
      path = sourceInfo.outPath or null;
      probe = builtins.tryEval (
        (sourceInfo.flake or true) && path != null && builtins.pathExists (path + "/flake.nix")
      );
    in
    probe.success && probe.value;

  declaredInputs =
    path:
    let
      f = path + "/flake.nix";
    in
    if builtins.pathExists f then (import f).inputs or { } else { };

  callFlake =
    {
      sourceInfo,
      inputs ? { },
      dir ? null,
      indirect ? (
        name:
        throw "pnix: the flake at ${toString sourceInfo.outPath} needs input '${name}', which is not resolved"
      ),
    }:
    let
      root = if dir == null then sourceInfo.outPath else dir;
      flake = import (root + "/flake.nix");

      wanted = builtins.functionArgs flake.outputs;
      unresolved = builtins.removeAttrs wanted (builtins.attrNames inputs ++ [ "self" ]);
      fallbacks = builtins.mapAttrs (n: _: indirect n) unresolved;

      finalInputs = fallbacks // inputs;

      outputs = flake.outputs (finalInputs // { self = result; });

      result =
        outputs
        // sourceInfo
        // {
          outPath = root;
          inputs = finalInputs;
          inherit outputs sourceInfo;
          _type = "flake";
        };
    in
    result;
}
