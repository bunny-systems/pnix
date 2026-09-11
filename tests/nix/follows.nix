{ }:
let
  mkFollows = import ../../pnix/resolver/eval/follows.nix;
  throws = v: !(builtins.tryEval (builtins.deepSeq v true)).success;

  # Stands in for resolve.nix's real evalNode: fetch the upstream node, then
  # evaluate it as a flake. The shape is what matters -- an attrset carrying
  # _type, not a bare path.
  # Signature matters: resolve.nix's real evalNode also takes the deep follows
  # policy, and a fake that omits it lets follows.nix go green while the real
  # wiring fails with "called without required argument".
  # Mirrors resolve.nix: a node marked `flake = false` still comes back as a
  # sourceInfo attrset, never a bare store path. Consumers read fields off it.
  evalNode =
    {
      lock,
      nodeName,
      follows,
    }:
    let
      node = lock.nodes.${nodeName};
      sourceInfo = {
        outPath = "/fake/store/${node.locked.rev}";
        inherit (node.locked) rev;
        lastModifiedDate = "20260101000000";
      };
    in
    if node.flake or true then
      sourceInfo
      // {
        _type = "flake";
        legacyPackages = "REAL-FLAKE";
        deepFollows = builtins.attrNames follows;
      }
    else
      sourceInfo;

  # An upstream source carrying its own flake.lock, on disk. Includes a
  # list-form input spec (`viaPath`), which is how every real lock encodes a
  # follows edge, and a `flake = false` node.
  #
  # A real file rather than builtins.toFile: under `nix-instantiate --eval` the
  # store path toFile returns is not valid, so readFile on it fails.
  upstreamLock = ./fixtures/locks/upstream/flake.lock;

  resolve = mkFollows {
    allInputs = {
      nixpkgs = "OURS";
    };
    allFollow = {
      nixpkgs = "nixpkgs";
    };
    pins = {
      follower = {
        excludeFollow = [ ];
      };
      loner = {
        excludeFollow = [ "nixpkgs" ];
      };
      pinned = {
        follows = {
          nixpkgs = "nixpkgs";
        };
      };
      empty = { };
    };
    sourceLockFor = _: upstreamLock;
    inherit evalNode;
  };

  # A pin whose source carries no flake.lock at all.
  resolveNoLock = mkFollows {
    allInputs = {
      nixpkgs = "OURS";
    };
    allFollow = { };
    pins.loner = { };
    sourceLockFor = _: null;
    inherit evalNode;
  };
in
[
  {
    name = "explicit follows wins";
    expr = resolve "follower" "nixpkgs" { follows = "nixpkgs"; };
    expected = "OURS";
  }
  {
    name = "a follows declared on the pin wins too";
    expr = resolve "pinned" "nixpkgs" { };
    expected = "OURS";
  }
  {
    name = "allFollow applies by default";
    expr = resolve "follower" "nixpkgs" { };
    expected = "OURS";
  }
  {
    name = "an empty follows is deliberately nothing";
    expr = resolve "follower" "nixpkgs" { follows = ""; };
    expected = { };
  }
  {
    name = "following a name that is not a pin throws";
    expr = throws (resolve "follower" "nixpkgs" { follows = "nosuch"; });
    expected = true;
  }
  {
    name = "excludeFollow falls through to the upstream lock";
    expr = (resolve "loner" "nixpkgs" { }).outPath;
    expected = "/fake/store/deadbeef";
  }
  {
    name = "and what it falls through to is an evaluated flake, not a path";
    expr = (resolve "loner" "nixpkgs" { }).legacyPackages;
    expected = "REAL-FLAKE";
  }
  {
    name = "a list-form input spec is walked from root";
    expr = (resolve "loner" "viaPath" { }).outPath;
    expected = "/fake/store/deadbeef";
  }
  {
    name = "an upstream node marked flake = false is not evaluated as a flake";
    expr = (resolve "loner" "bare" { }) ? _type;
    expected = false;
  }
  {
    name = "step 3 receives the deep follows policy";
    expr = (resolve "follower" "viaPath" { }).deepFollows;
    expected = [ "nixpkgs" ];
  }
  {
    name = "a pin that opted out does not hand the policy down either";
    expr = (resolve "loner" "viaPath" { }).deepFollows;
    expected = [ ];
  }
  {
    name = "but it is still a sourceInfo attrset, never a bare path";
    expr = (resolve "loner" "bare" { }).lastModifiedDate;
    expected = "20260101000000";
  }
  {
    name = "a flake = false node still carries its outPath";
    expr = (resolve "loner" "baregit" { }).outPath;
    expected = "/fake/store/beef";
  }
  {
    name = "unknown sub-input with no upstream entry is empty";
    expr = resolve "loner" "nowhere" { };
    expected = { };
  }
  {
    name = "no upstream lock at all is empty, not an error";
    expr = resolveNoLock "loner" "nixpkgs" { };
    expected = { };
  }

  # An input the upstream `outputs` merely asks for, which it never declared
  # and never locked. Handing back `{ }` made the upstream fail far away --
  # `nixpkgs.legacyPackages.…` on an empty set -- instead of here, where the
  # remedy is. resolve.nix's transitive path always threw; only the top-level
  # path was silent, and the two disagreeing is what made it a bug.
  {
    name = "an indirect input nothing resolved throws, rather than being empty";
    expr = throws (resolveNoLock "loner" "nixpkgs" { _indirect = true; });
    expected = true;
  }
  {
    name = "a declared input nothing resolved is still empty";
    expr = resolveNoLock "loner" "nixpkgs" { };
    expected = { };
  }
  {
    name = "an indirect input the policy answers for does not throw";
    # `resolve` carries an allFollow policy; step 2 answers before step 4a can.
    expr = throws (resolve "loner" "nixpkgs" { _indirect = true; });
    expected = false;
  }
]
