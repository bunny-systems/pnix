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
}:
{
  name,
  src,
  node,
}:
let
  patches = node.patches or [ ];

  applied = patchPkgs.applyPatches {
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
    patchFlags = [
      "-p1"
      "-F0"
      "--no-backup-if-mismatch"
    ];
  };
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
