# pnix-managed. delete this line to take ownership; pnix will leave it alone.
# SPDX-License-Identifier: EUPL-1.2
{
  lockFile ? ../pins.lock.json,

  allFollow ? { },

  overrides ? { },

  overrideVar ? "PNIX_OVERRIDE",

  nixpkgsPin ? "nixpkgs",
}:
let
  fetchers = import ./fetchers.nix { };
  fl = import ./flake.nix;
  upstream = import ./upstream.nix;
  mkDate = import ./date.nix;

  fetchPatch =
    patch:
    if patch.kind or null == "path" then
      builtins.dirOf lockFile + ("/" + patch.path)
    else
      fetchers.file {
        inherit (patch) url;
        hash = patch.hash;
      };

  SCHEMA = 4;

  doc = builtins.fromJSON (builtins.readFile lockFile);
  schema = doc.schema or (throw "pnix: ${toString lockFile} has no `schema`");
  pins =
    if schema != SCHEMA then
      throw "pnix: ${toString lockFile} is lock schema ${toString schema} but this resolver speaks ${toString SCHEMA}. Re-run `pnix update` with a matching pnix."
    else
      doc.pins or { };

  envOverrides =
    if overrideVar == null then
      { }
    else
      import ./override.nix {
        inherit pins;
        var = overrideVar;
      };

  allOverrides = overrides // envOverrides;

  fetched = builtins.mapAttrs (name: node: allOverrides.${name} or (fetchers.fetch node)) pins;

  pathOf = v: if builtins.isAttrs v && v ? outPath then v.outPath else v;

  rawSources = builtins.mapAttrs (_: pathOf) fetched;

  patchPkgs =
    if rawSources ? ${nixpkgsPin} then
      import rawSources.${nixpkgsPin} { }
    else
      throw "pnix: a pin declares patches, which need a nixpkgs to apply them, but there is no pin called '${nixpkgsPin}'. Pass `nixpkgsPin` to name it.";

  applyTo = import ./patch.nix { inherit patchPkgs fetchPatch; };

  patchedSources = builtins.mapAttrs (
    name: src:
    applyTo {
      inherit name src;
      node = pins.${name};
    }
  ) rawSources;

  sources = builtins.mapAttrs (_: p: p.outPath) patchedSources;

  sourceInfoFrom =
    outPath: node:
    {
      inherit outPath;
    }
    // (
      if node ? rev then
        {
          inherit (node) rev;
          shortRev = builtins.substring 0 7 node.rev;
        }
      else
        { }
    )
    // (
      if node ? lastModified then
        {
          inherit (node) lastModified;
          lastModifiedDate = mkDate node.lastModified;
        }
      else
        { }
    )
    // (if node ? narHash then { inherit (node) narHash; } else { })
    // (if node ? version then { inherit (node) version; } else { })
    // (
      if node ? fetch && node.fetch ? hash && !(node ? narHash) then
        { narHash = node.fetch.hash; }
      else
        { }
    )
    // (if node ? flake then { inherit (node) flake; } else { });

  flakeDirOf = outPath: node: if node ? dir then outPath + ("/" + node.dir) else outPath;

  sourceLockFor =
    name:
    let
      p = sources.${name} + "/flake.lock";
    in
    if builtins.pathExists p then p else null;

  evalLock =
    lock: follows:
    let
      followed =
        name:
        if follows ? ${name} then
          allInputs.${follows.${name}}
            or (throw "pnix: deep follows target '${follows.${name}}' is not a pin")
        else
          null;

      nodes = builtins.mapAttrs (
        nodeName: node:
        let
          locked = upstream.normalize node.locked;
          raw = fetchers.fetch { fetch = locked; };
          src = pathOf raw;
          sourceInfo = sourceInfoFrom src (
            node.locked
            // (if builtins.isAttrs raw then builtins.removeAttrs raw [ "outPath" ] else { })
            // (if node ? flake then { inherit (node) flake; } else { })
          );
          dir = flakeDirOf src node.locked;

          subInput =
            subName:
            let
              viaFollows = followed subName;
              child = upstream.inputNode lock nodeName subName;
            in
            if viaFollows != null then
              viaFollows
            else if child == null then
              { }
            else
              nodes.${child};
        in
        if (node.flake or true) && fl.isFlake (sourceInfo // { outPath = dir; }) then
          fl.callFlake {
            inherit sourceInfo dir;
            inputs = builtins.mapAttrs (subName: _: subInput subName) (node.inputs or { });
            indirect =
              name:
              let
                viaFollows = followed name;
              in
              if viaFollows != null then
                viaFollows
              else
                throw "pnix: upstream flake '${nodeName}' needs input '${name}', which is in neither its flake.lock nor the follows policy";
          }
        else
          sourceInfo
      ) (builtins.removeAttrs lock.nodes [ lock.root ]);
    in
    nodes;

  evalNode =
    {
      lock,
      nodeName,
      follows,
    }:
    (evalLock lock follows).${nodeName};

  resolveSub = import ./follows.nix {
    inherit
      allInputs
      allFollow
      pins
      sourceLockFor
      evalNode
      ;
  };

  allInputs = builtins.mapAttrs (
    name: node:
    let
      src = sources.${name};
      patch = patchedSources.${name};
      extra = builtins.removeAttrs (if builtins.isAttrs fetched.${name} then fetched.${name} else { }) [
        "outPath"
      ];
      sourceInfo = sourceInfoFrom src (node // extra);
      dir = flakeDirOf src node;

      mayProbe = !patch.patched || patch.importable;
    in
    if patch.patched && !patch.importable then
      builtins.trace "pnix: '${name}' is patched; using it as a source only. Set `importable = true;` if its modules must be imported (costs an import-from-derivation)." sourceInfo
    else if mayProbe && fl.isFlake (sourceInfo // { outPath = dir; }) then
      fl.callFlake {
        inherit sourceInfo dir;
        inputs = builtins.mapAttrs (
          subName: spec: resolveSub name subName (if builtins.isAttrs spec then spec else { })
        ) (fl.declaredInputs dir);
        indirect = subName: resolveSub name subName { _indirect = true; };
      }
    else
      sourceInfo
  ) pins;
in
allInputs
