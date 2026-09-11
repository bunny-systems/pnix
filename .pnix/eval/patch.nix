# pnix-managed. delete this line to take ownership; pnix will leave it alone.
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
    importable = node.importable or false;
    unpatched = src;
  }
