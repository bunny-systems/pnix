# The real entry point. flake.nix is a passthrough over this.
#
# nixpkgs comes from this repo's own .pnix/pins.lock.json, resolved by this
# repo's own vendored resolver -- so the dev environment is an integration test
# of pnix, and the flake has no inputs at all.
{
  system ? builtins.currentSystem,
  # Named `sources` rather than `pins`: a binding called `pins` would make this
  # file a declaration candidate for pnix's own collector.
  sources ? import ./.pnix { },
  pkgs ? import sources.nixpkgs.outPath {
    inherit system;
    config = { };
    overlays = [ ];
  },
}:
rec {
  packages.default = pkgs.callPackage ./package.nix { };
  packages.pnix = packages.default;

  devShells.default = import ./shell.nix { inherit pkgs; };

  formatter = pkgs.nixfmt-tree;

  inherit pkgs sources;
}
