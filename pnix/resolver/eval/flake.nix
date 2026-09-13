# What a flake actually is, in builtins.
#
# A flake is not a special object. It is a directory containing a `flake.nix`
# that evaluates to an attrset with two keys: `inputs`, which is data, and
# `outputs`, which is a function. Reading the data is `declaredInputs`; calling
# the function is `callFlake`. Everything the flake CLI adds on top of that is
# bookkeeping.
#
# No experimental features are involved anywhere: `import ./flake.nix` and
# applying a function are the whole mechanism.
rec {

  # Keys that mark an attrset as an *unresolved input declaration* rather than
  # an already-resolved value. Only ever consulted after outPath/_type have
  # ruled a resolved value out, so the list can stay short without becoming a
  # correctness risk.
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

  # follows | spec | direct, decided structurally -- no input is ever
  # recognised by name.
  #
  #   { follows = "nixpkgs"; }                       -> follows
  #   { url = "github:o/r"; inputs.x.follows = …; }  -> spec
  #   { outPath = "/nix/store/…"; lib = { }; }       -> direct
  #
  # An empty `follows = ""` is still a follows: it is how a flake says "this
  # input is deliberately nothing", which is different from not mentioning it.
  classify =
    v:
    if !builtins.isAttrs v then
      "direct"
    else if v ? follows then
      "follows"
    # A resolved value carries its store path and its flake marker. Those are
    # the discriminator, not the presence of any particular spec key: a spec
    # may legitimately carry keys this file has never heard of.
    else if v ? outPath || v ? _type then
      "direct"
    else if builtins.any (k: v ? ${k}) specKeys then
      "spec"
    else
      "direct";

  # `flake = false` is the pin saying "fetch this, do not evaluate it" -- a
  # statement about the source, so it belongs here rather than in every caller.
  #
  # `or null` handles a sourceInfo with no outPath at all. It is the only guard
  # here, and this used to be wrapped in tryEval as well -- wrongly. Measured,
  # tryEval catches none of the three things that comment claimed:
  #
  #   pathExists on a path outside the allowed roots, under either --pure-eval
  #   or --restrict-eval, does not raise at all; it answers false.
  #   An outPath that `+` cannot extend raises a coercion error, which tryEval
  #   does not catch -- the same hole as a missing attribute.
  #   A merely missing flake.nix needs no guard either; pathExists answers false.
  #
  # What it did catch was the one thing that must never be caught: a `throw`
  # raised while forcing outPath. Every such throw is pnix reporting a broken
  # declaration -- no fetcher for this kind, no nixpkgs to apply patches with --
  # and swallowing it turned the pin into "not a flake", so the consumer saw
  # `attribute 'lib' missing` from its own file instead of the real cause.
  isFlake =
    sourceInfo:
    let
      path = sourceInfo.outPath or null;
    in
    (sourceInfo.flake or true) && path != null && builtins.pathExists (path + "/flake.nix");

  # The data half: every input the flake declares, unresolved.
  declaredInputs =
    path:
    let
      f = path + "/flake.nix";
    in
    if builtins.pathExists f then (import f).inputs or { } else { };

  # The function half.
  #
  # `self` is recursive: outputs receives it, and it is built from what outputs
  # returns. That terminates only because Nix is lazy -- `{ self = result; }` is
  # already in weak head normal form, so `result` stays a thunk until an output
  # attribute is actually forced, by which time outputs has returned. A strict
  # language would need an explicit two-phase construction.
  #
  # Indirect inputs: `outputs = { self, nixpkgs, ... }:` in a flake that
  # declares no nixpkgs input still expects one. `builtins.functionArgs` reports
  # what a function wants before it is called -- the same builtin the collector
  # uses to stub module arguments.
  #
  # An unresolvable input becomes a throwing stub rather than `{ }`: it keeps
  # the call itself working, so one bad input does not take the whole
  # evaluation with it, while still failing loudly and by name if anything
  # actually reads it.
  callFlake =
    {
      sourceInfo,
      inputs ? { },
      # The directory holding flake.nix, when it is not the source root. A
      # flake's own outPath is the subdirectory; sourceInfo.outPath stays the
      # repo root, which is what `self.sourceInfo` is for.
      dir ? null,
      # What to supply for an input `outputs` asks for that was not resolved.
      # Default is a throwing stub: the call keeps working, so one bad input
      # does not take the evaluation with it, but reading it fails by name.
      # resolve.nix overrides this to consult the deep follows policy first,
      # which is the only way an *indirect* input can follow anything.
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

      # sourceInfo after outputs: outPath, rev and lastModified describe the
      # source and an output attribute must not be able to shadow them. This is
      # the order Nix's own call-flake.nix uses.
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
