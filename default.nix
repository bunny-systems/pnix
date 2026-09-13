# Entrypoint that just calls the package file for `nix-build`.
# Can also be used to install pnix with `nix-env` and other commands that use `default.nix`, although this isn't recommended.
{
  sources ? import ./.pnix { },
  pkgs ? import sources.nixpkgs {
    config = { };
    overlays = [ ];
  },
}:
pkgs.callPackage ./package.nix { }
