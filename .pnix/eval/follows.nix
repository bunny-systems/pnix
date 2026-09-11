# pnix-managed. delete this line to take ownership; pnix will leave it alone.
# SPDX-License-Identifier: EUPL-1.2
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

  deepFollows = builtins.removeAttrs allFollow (pin.excludeFollow or [ ]);

  explicit = declaredSpec.follows or (pin.follows or { }).${subName} or null;

  policy = if excluded then null else allFollow.${subName} or null;

  target = if explicit != null then explicit else policy;

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
