# Apply a pin's patches to its fetched source.
#
# **Case A, the default: no IFD.** `applyPatches` produces a derivation. If the
# pin is a package *source* -- `src = inputs.finit;`, then `overrideAttrs` or an
# overlay -- nothing reads it during evaluation, so the derivation is simply
# referenced and built later like any other. Every patch-shaped thing in
# consumer #1 is this case, and not by luck: patching an input to change a
# *module* is nearly always wrong when mkForce, overlays and disabledModules
# exist.
#
# The trap is that "no IFD" is a property of what the *caller* does, and one
# probe breaks it. `isFlake` calls `pathExists (outPath + "/flake.nix")`, and on
# an unbuilt derivation that forces realisation. So a patched pin is never
# probed for flake-ness unless the declaration asks for it.
#
# **Case B, behind `importable = true`: IFD.** The pin is imported as modules,
# so the patched tree must exist before evaluation can finish. Unavoidable --
# there is no builtins-only patch -- and loud, because it will stall a rebuild
# while it builds, and fail outright under `--option allow-import-from-derivation
# false`.
#
# `applyPatches` needs a `pkgs`, which lives inside the consumer's own
# evaluation and would be circular. `patchPkgs` is a plain
# `import <nixpkgs source> { }` built from the *unpatched* fetch of the nixpkgs
# pin: no overlays, no config, and no cycle even if nixpkgs itself is patched.
{
  patchPkgs,
  fetchPatch,
  # May be null: `builtins.currentSystem` does not exist under pure evaluation.
  # Only the no-`patchedHash` branch below cares, because only there does
  # `system` reach the output path. Checked per pin rather than in `patchPkgs`,
  # which is one shared value for every patched pin -- a throw there fires for a
  # pin that has a hash because some unrelated pin does not.
  system,
}:
{
  name,
  src,
  node,
}:
let
  patches = node.patches or [ ];
  hash = node.patchedHash or null;

  args = {
    name = "${name}-patched";
    inherit src;
    patches = map fetchPatch patches;

    # **A patch applies exactly or the build fails.** GNU patch defaults to
    # fuzzing context lines and, when it does, writing the original alongside as
    # `.orig` -- so a patch generated against a different tree appears to
    # succeed while landing hunks it only approximately recognised.
    #
    # Measured on the design's own worked example. finit PR #181 against the rev
    # the design pins produces `Hunk #1 succeeded at 312 with fuzz 2 (offset 11
    # lines)` and leaves `src/finit.c.orig` and `src/service.c.orig` in the
    # result. The cause is that a forge's PR diff spans from the **merge base**
    # of base and head, which is not the commit you pinned; the diff is only
    # guaranteed against the tree it was generated from.
    #
    # `-F0` forbids fuzz while still allowing a hunk to land at a different line
    # number, which is ordinary and safe. `--no-backup-if-mismatch` keeps a
    # failed apply from leaving debris in a tree that is about to be built.
    # Under a fixed-output derivation it does more than that: an inexact apply
    # would silently redefine the tree the recorded hash is meant to name.
    patchFlags = [
      "-p1"
      "-F0"
      "--no-backup-if-mismatch"
    ];
  };

  # Content-addressed: the path is a function of (name, hash, mode) alone, so
  # neither `system` nor the nixpkgs supplying `patch` reaches it. Sound only
  # because `applyPatches` compiles nothing -- its phases are
  # `unpackPhase patchPhase installPhase` and its builder is stdenvNoCC, so the
  # output is a source tree, byte-identical on every platform. Measured on
  # x86_64-linux and i686-linux: different toolchains, one hash.
  #
  # `allowSubstitutes` has to go through `overrideAttrs`. `applyPatches` is built with
  # nixpkgs' `extendMkDerivation`, whose own arguments win over the caller's, so
  # passing it in `args` is silently dropped -- measured, it still lands as
  # false. And a fixed-output derivation does honour it (also measured, nix
  # 2.34.8), contrary to the folklore that they are always substitutable, so
  # this flip is what lets one machine's build serve the rest from a cache.
  fixed =
    (patchPkgs.applyPatches (
      args
      // {
        outputHash = hash;
        outputHashAlgo = "sha256";
        outputHashMode = "recursive";
      }
    )).overrideAttrs
      (_: {
        allowSubstitutes = true;
      });

  # No hash in the lock: today's derivation, whose path moves whenever the
  # nixpkgs applying the patch moves. Traced rather than thrown, so a lock
  # migrated from schema 4 keeps evaluating until the next `pnix update`.
  legacy =
    if system == null then
      throw "pnix: '${name}' is patched and its lock has no patchedHash, so applying it needs a system to build for -- and this evaluation is pure, so it has none. Run `pnix update` to record a patchedHash, or pass `system`, e.g. `import ./.pnix { system = \"x86_64-linux\"; }`."
    else
      builtins.trace "pnix: '${name}' is patched but its lock has no patchedHash, so its store path depends on which nixpkgs applied the patch. Re-run `pnix update`." (
        patchPkgs.applyPatches args
      );

  applied = if hash == null then legacy else fixed;
in
if patches == [ ] then
  {
    outPath = src;
    patched = false;
  }
else
  {
    outPath = applied;
    patched = true;
    # Whether the caller may look inside without an import-from-derivation.
    importable = node.importable or false;
    unpatched = src;
  }
