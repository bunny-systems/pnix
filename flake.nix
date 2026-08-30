{
  description = ''
    initial flake for inotus pinning for pnix
    TODO: remove and replace with bootstrap or atleast make it
  '';

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      nixpkgs,
      flake-utils,
      ...
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            uv
            python314
          ];

          shellHook = ''
            echo "pnix DevShell"
          '';
        };

        formatter = pkgs.nixfmt-tree;
      }
    );
}
