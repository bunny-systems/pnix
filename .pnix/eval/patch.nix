# pnix-managed. delete this line to take ownership; pnix will leave it alone.
# SPDX-License-Identifier: EUPL-1.2
{
  patchPkgs,
  fetchPatch,
  system,
}:
{
  name,
  src,
  node,

  overridden ? false,
}:
let
  patches = node.patches or [ ];
  hash = node.patchedHash or null;

  args = {
    name = "${name}-patched";
    inherit src;
    patches = map fetchPatch patches;

    patchFlags = [
      "-p1"
      "-F0"
      "--no-backup-if-mismatch"
    ];
  };

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

  legacy =
    if system == null then
      throw "pnix: '${name}' is patched and its lock has no patchedHash, so applying it needs a system to build for -- and this evaluation is pure, so it has none. Run `pnix update` to record a patchedHash, or pass `system`, e.g. `import ./.pnix { system = \"x86_64-linux\"; }`."
    else
      builtins.trace "pnix: '${name}' is patched but its lock has no patchedHash, so its store path depends on which nixpkgs applied the patch. Re-run `pnix update`." (
        patchPkgs.applyPatches args
      );

  applied = if hash == null then legacy else fixed;

  verbatim = {
    outPath = src;
    patched = false;
  };
in
if patches == [ ] then
  verbatim

else if overridden then
  builtins.trace "pnix: '${name}' is overridden, so its ${toString (builtins.length patches)} declared patch${if builtins.length patches == 1 then "" else "es"} ${if builtins.length patches == 1 then "is" else "are"} not applied" verbatim
else
  {
    outPath = applied;
    patched = true;
    importable = node.importable or false;
    unpatched = src;
  }
