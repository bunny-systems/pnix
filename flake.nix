# Flake entrypoint for pnix.
# Not recommended to add as an actual flake input -- just kept for `nix run`, `nix develop`, and `nix fmt`.
{
  outputs =
    { self }:
    let
      inherit (import ./.pnix { }) nixpkgs;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forEachSystem =
        f:
        builtins.listToAttrs (
          map (system: {
            name = system;
            value = f (import nixpkgs { inherit system; });
          }) systems
        );
    in
    {
      packages = forEachSystem (pkgs: {
        pnix = pkgs.callPackage ./package.nix { };
        default = self.packages.${pkgs.stdenv.hostPlatform.system}.pnix;
      });
      devShells = forEachSystem (pkgs: {
        default = import ./shell.nix { inherit pkgs; };
      });
      formatter = forEachSystem (pkgs: pkgs.nixfmt-tree);
    };
}
