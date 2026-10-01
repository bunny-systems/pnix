# pins.lock.json -> evaluated inputs.
#
# This is the whole eval-time half: read the lock, fetch each pin, evaluate the
# ones that are flakes, and resolve their sub-inputs through the four-step
# order in follows.nix. Pure builtins, no lib, no experimental features -- a
# fresh `git clone && nixos-rebuild --file .` must work with Nix alone.
{
  # Default suits the vendored layout: <consumer>/.pnix/eval/resolve.nix, one
  # level below <consumer>/.pnix/pins.lock.json.
  lockFile ? ../pins.lock.json,

  # name -> pin name. The global "everything follows our nixpkgs" policy; a pin
  # opts out per-name with excludeFollow.
  allFollow ? { },

  # name -> path or evaluated value, replacing whatever the lock says. The
  # programmatic form; PNIX_OVERRIDE is layered on top of it below.
  #
  # This one substitutes a pin's *source*: declared patches are still applied
  # to whatever it supplies, since a parent composing a child's pins is not
  # claiming to know which patches the child needs. The environment layer is
  # the one that replaces the resolved input outright -- see `patchedSources`.
  overrides ? { },

  # Set to null to ignore the environment entirely -- useful in tests, and for
  # a caller that wants a reproducible evaluation regardless of the shell.
  overrideVar ? "PNIX_OVERRIDE",

  # Which pin supplies the nixpkgs used to apply patches. Only consulted when
  # some pin actually declares patches.
  nixpkgsPin ? "nixpkgs",

  # Which system builds a patched pin. Only consulted when some pin actually
  # declares patches -- applying one is a derivation, and a derivation needs a
  # system.
  #
  # `builtins.currentSystem` does not exist under pure evaluation, which is
  # every `nix build .#...` of a flake. With a `patchedHash` in the lock that no
  # longer matters for the *result*: the output is content-addressed, so a wrong
  # value costs only the ability to build the tree natively, never correctness.
  # So this falls back to a literal below, and `patch.nix` raises per pin where
  # the pin has no hash and `system` still decides its path.
  system ? builtins.currentSystem or null,

  # pnix's own lock-time entry point: `{ <pin> = <the applyPatches derivation>; }`
  # for the pins that declare patches. `pnix update` builds these to learn their
  # hashes. It shares every line of the real path deliberately -- a second
  # expression that built the tree its own way would be free to disagree with
  # the resolver, and the hash would then be wrong in the one way nothing checks.
  patchedOnly ? false,
}:
let
  fetchers = import ./fetchers.nix { };
  fl = import ./flake.nix;
  upstream = import ./upstream.nix;
  mkDate = import ./date.nix;

  # A patch is fetched with the `file` primitive: the flat hash of a diff, not
  # the NAR hash of a tree.
  fetchPatch =
    patch:
    if patch.kind or null == "path" then
      # A local patch lives in the consumer's own repo, recorded relative to the
      # lock so a clone on another machine still finds it.
      builtins.dirOf lockFile + ("/" + patch.path)
    else
      fetchers.file {
        inherit (patch) url;
        hash = patch.hash;
      };

  SCHEMA = 5;

  doc = builtins.fromJSON (builtins.readFile lockFile);
  schema = doc.schema or (throw "pnix: ${toString lockFile} has no `schema`");
  pins =
    if schema != SCHEMA then
      throw "pnix: ${toString lockFile} is lock schema ${toString schema} but this resolver speaks ${toString SCHEMA}. Re-run `pnix update` with a matching pnix."
    else
      doc.pins or { };

  # Fetching is the only place a pin's declaration fields are read. Everything
  # downstream sees a store path.
  #
  # The primitives do not agree on return type and cannot be made to:
  # `fetchTarball` and `fetchurl` return a path, while `fetchGit` returns an
  # attrset carrying outPath, rev, shortRev, lastModified and narHash. An
  # attrset with an outPath coerces to its path in most positions, so taking it
  # at face value appears to work and then fails somewhere far away -- normalise
  # here instead, and keep fetchGit's extra fields, which are better provenance
  # than the lock has for a git pin.
  # The environment wins over the `overrides` argument. Its whole purpose is
  # "ignore what this config says, for this one run".
  envOverrides =
    if overrideVar == null then
      { }
    else
      import ./override.nix {
        inherit pins;
        var = overrideVar;
      };

  # Lowest to highest: what this config says, then what this one command says.
  # There is deliberately no layer in between: a machine-local override file
  # would make one host build differently from what the committed tree says,
  # with nothing in a diff to explain it. An override should be visible in the
  # command that causes it.
  allOverrides = overrides // envOverrides;

  fetched = builtins.mapAttrs (name: node: allOverrides.${name} or (fetchers.fetch node)) pins;

  pathOf = v: if builtins.isAttrs v && v ? outPath then v.outPath else v;

  rawSources = builtins.mapAttrs (_: pathOf) fetched;

  # `applyPatches` needs a pkgs. Built from the *unpatched* nixpkgs source, so
  # there is no cycle even if the nixpkgs pin itself carries patches.
  #
  # `config` and `overlays` are passed explicitly because nixpkgs' defaults for
  # them are not empty: impure.nix reads $NIXPKGS_CONFIG or
  # ~/.config/nixpkgs/config.nix, and impure-overlays.nix reads $NIXPKGS_OVERLAYS
  # or ~/.config/nixpkgs/overlays. Left alone, whatever happens to be in the
  # invoking user's home would decide how a pin's patches get applied.
  # A literal is safe here only because a patched pin's path comes from its
  # recorded hash; on a machine of another arch, pass `system` so the tree can
  # actually be built when it is not already in the store or a cache.
  buildSystem = if system == null then "x86_64-linux" else system;

  patchPkgs =
    if !(rawSources ? ${nixpkgsPin}) then
      throw "pnix: a pin declares patches, which need a nixpkgs to apply them, but there is no pin called '${nixpkgsPin}'. Pass `nixpkgsPin` to name it."
    else
      import rawSources.${nixpkgsPin} {
        system = buildSystem;
        config = { };
        overlays = [ ];
      };

  applyTo = import ./patch.nix { inherit patchPkgs fetchPatch system; };

  patchedSources = builtins.mapAttrs (
    name: src:
    applyTo {
      inherit name src;
      node = pins.${name};
      # Deliberately the environment layer only, not `allOverrides`. The two
      # mean different things: the `overrides` argument substitutes where a
      # pin's *source* comes from, which is how a parent project composes a
      # child's pins (and how the test harness stands in for the fetchers), so
      # the child's declared patches still belong on it. `PNIX_OVERRIDE`
      # replaces the input itself for one impure run.
      overridden = envOverrides ? ${name};
    }
  ) rawSources;

  sources = builtins.mapAttrs (_: p: p.outPath) patchedSources;

  # Only the fields that describe the *source*. A pin's excludeFollow, patches
  # and provenance are pnix bookkeeping and must not leak onto the flake result.
  #
  # This is also the exhaustive list of lock fields the vendored half reads,
  # alongside `fetch`, `dir`, `follows` and `excludeFollow`. Everything else in
  # a pin entry is provenance and can change without a schema bump.
  #
  # `shortRev` and `lastModifiedDate` are not decoration. nixpkgs' own flake
  # builds its version string as
  #   ".${substring 0 8 (self.lastModifiedDate or self.lastModified or "19700101")}.${self.shortRev or "dirty"}"
  # so a missing pair renames every NixOS system derivation to
  # `...19700101.dirty` and changes its store path. `lastModifiedDate` has to
  # come from the lock: Nix formats it in C++ and there is no strftime in
  # builtins.
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
    # A patched pin's `outPath` is the patched tree, so its `narHash` has to be
    # that tree's hash and not the unpatched fetch's -- the two described
    # different trees until schema 5 gave the patched one a name.
    // (
      if node ? patchedHash then
        { narHash = node.patchedHash; }
      else if node ? narHash then
        { inherit (node) narHash; }
      else if node ? fetch && node.fetch ? hash then
        { narHash = node.fetch.hash; }
      else
        { }
    )
    # A channel has no rev; its version string is the only thing that names
    # which nixpkgs it is. Unlike `patches`, a resolver that predates this field
    # just does not expose it -- a missing attribute errors loudly rather than
    # producing a quietly wrong result, so it needs no schema bump.
    // (if node ? version then { inherit (node) version; } else { })
    // (if node ? flake then { inherit (node) flake; } else { });

  # The directory holding flake.nix. `dir` was in the schema from the start but
  # no fetcher ever read it, so it was accepted and silently ignored.
  # Parenthesised on purpose. `p + "/" + d` associates left to right, and
  # `<path> + "/"` normalises the trailing slash away, so the naive spelling
  # yields `/store/…-sourcesub` rather than `/store/…-source/sub`.
  flakeDirOf = outPath: node: if node ? dir then outPath + ("/" + node.dir) else outPath;

  sourceLockFor =
    name:
    let
      p = sources.${name} + "/flake.lock";
    in
    if builtins.pathExists p then p else null;

  # Step 3's recursion: evaluate a node of some upstream flake.lock, and its
  # inputs, and theirs. `follows` is the deep policy -- allFollow minus the
  # originating pin's excludeFollow -- so a transitive dependency still lands on
  # our nixpkgs unless the pin opted out.
  #
  # Memoised per (lock, follows) by building the whole node set as one lazy
  # attrset and recursing through it: a diamond in the upstream graph is then
  # fetched and evaluated once instead of once per path. Nix shares thunks
  # within an attrset but not across separate calls, so the sharing has to be
  # made structural like this.
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
            # An input the upstream `outputs` asks for that its own lock never
            # records. The deep policy can still answer for it; only then does
            # it become a throwing stub.
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
      # fetchGit knows the rev and lastModified first-hand; prefer that over
      # whatever the lock recorded.
      extra = builtins.removeAttrs (if builtins.isAttrs fetched.${name} then fetched.${name} else { }) [
        "outPath"
      ];
      # `patchedHash` names the patched tree, so it is only this pin's narHash
      # when there *is* one. An overridden pin skips its patches, and would
      # otherwise report the locked hash of a tree that was never built -- and
      # report it over an attrset override's own narHash, which `extra` carries.
      sourceInfo = sourceInfoFrom src (
        (if patch.patched then node else builtins.removeAttrs node [ "patchedHash" ]) // extra
      );
      dir = flakeDirOf src node;

      # A patched source is an unbuilt derivation. Probing it for a flake.nix
      # realises it -- import from derivation -- so that probe only happens when
      # the declaration asks for it with `importable = true`.
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
        # `_indirect` is how follows.nix tells an input the flake merely asked
        # for from one it declared: the first must not silently resolve to `{ }`.
        indirect = subName: resolveSub name subName { _indirect = true; };
      }
    else
      sourceInfo
  ) pins;
in
if patchedOnly then
  builtins.listToAttrs (
    builtins.concatMap (
      name:
      if patchedSources.${name}.patched then
        [
          {
            inherit name;
            value = patchedSources.${name}.outPath;
          }
        ]
      else
        [ ]
    ) (builtins.attrNames pins)
  )
else
  allInputs
