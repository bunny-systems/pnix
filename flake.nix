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
          packages = with pkgs; [
            uv
            ruff
            python314
            python314Packages.pytest
          ];

          shellHook = ''
            export UV_PYTHON_DOWNLOADS=never
            export UV_NO_MANAGED_PYTHON=1
            echo "pnix DevShell"
          '';
        };

        formatter = pkgs.nixfmt-tree;
      }
    );
}
