# A passthrough over default.nix, kept only so `nix develop` and `nix build`
# work for people who expect them.
#
# **It has no inputs.** nixpkgs is pinned in .pnix/pins.lock.json by pnix itself, so
# there is nothing for a flake.lock to hold and nothing that needs the flakes
# feature to resolve. Everything here is also reachable without it:
#
#   nix-shell                      instead of  nix develop
#   nix-build -A packages.default  instead of  nix build
{
  description = "pnix — Nix-native input pinning. See default.nix; this file only forwards to it.";

  outputs =
    { self }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      each =
        f:
        builtins.listToAttrs (
          map (system: {
            name = system;
            value = f system;
          }) systems
        );
      entry = system: import ./. { inherit system; };
    in
    {
      packages = each (system: (entry system).packages);
      devShells = each (system: (entry system).devShells);
      formatter = each (system: (entry system).formatter);
    };
}
