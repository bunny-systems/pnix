# pnix-managed. delete this line to take ownership; pnix will leave it alone.
# Sub-input resolution: what a pinned flake's own inputs resolve to.
#
# The obvious implementation falls back to the caller's top-level pins and
# returns { } otherwise, which flattens the graph and makes excludeFollow
# meaningless -- opting out of following would just yield an empty attrset.
# Measured on the reference config: 13 distinct nixpkgs revisions appear across
# its pins' own flake.lock files, and 9 of its 21 pins deliberately opt out.
#
# Four-step order:
#   1. explicit per-sub-input `follows`
#   2. the `allFollow` policy, unless this pin lists the name in excludeFollow
#   3. the pinned source's own flake.lock
#   4. { }
#
# Step 3 returns an *evaluated flake*, not a store path. The pinned flake's own
# outputs will immediately do things like `nixpkgs.legacyPackages.${system}` on
# whatever it is handed, and a path has no such attribute -- so a fake fetcher
# returning a bare path will hide the bug rather than catch it. `evalNode` is
# injected for that reason; resolve.nix supplies the real one, which recurses
# through the upstream lock.
#
# `flake = false` on an upstream node is **not** handled here. It used to be --
# fetch it and hand back the store path -- and that was wrong twice over: a
# non-flake input still gets a sourceInfo attrset in real Nix, not a bare path,
# and consumers read fields off it. Measured: niri-nix's packages/niri.nix
# builds its version from `src.lastModifiedDate`, which on a path is
# `error: expected a set but found a string with context`. evalNode already
# returns a sourceInfo for such a node, so this file just asks for it and the
# rule lives in one place.
{
  allInputs,
  allFollow,
  pins,
  sourceLockFor,
  evalNode,
}:
let
  upstream = import ./upstream.nix;
in
hostName: subName: declaredSpec:
let
  pin = pins.${hostName} or { };

  excluded = builtins.elem subName (pin.excludeFollow or [ ]);

  # The policy that keeps applying below step 3. A transitive dependency two
  # levels down should still land on our nixpkgs unless this pin opted out, so
  # the deep policy is allFollow minus this pin's excludeFollow -- the same
  # rules as step 2, minus the per-sub-input `follows` that only apply here.
  deepFollows = builtins.removeAttrs allFollow (pin.excludeFollow or [ ]);

  # 1. explicit per-sub-input follows, from the flake's own declaration or
  #    from the consumer's pin.
  explicit = declaredSpec.follows or (pin.follows or { }).${subName} or null;

  # 2. global policy, unless this pin opts out
  policy = if excluded then null else allFollow.${subName} or null;

  target = if explicit != null then explicit else policy;

  # 3. the pinned source's own flake.lock
  lockPath = sourceLockFor hostName;
  doc =
    if lockPath == null || !builtins.pathExists lockPath then
      null
    else
      builtins.fromJSON (builtins.readFile lockPath);
  nodeName = if doc == null then null else upstream.inputNode doc doc.root subName;
  node = if nodeName == null then null else doc.nodes.${nodeName};
in
if target != null then
  # An empty follows is a flake saying "this input is deliberately nothing",
  # which is not the same as never mentioning it.
  if target == "" then
    { }
  else
    allInputs.${target}
      or (throw "pnix: '${hostName}' follows '${target}' for input '${subName}', which is not a pin")
else if node != null then
  evalNode {
    lock = doc;
    inherit nodeName;
    follows = deepFollows;
  }
else
  { }
