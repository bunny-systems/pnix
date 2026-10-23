{
  sources ? import ./.pnix { },
  system ? builtins.currentSystem,
  pkgs ? import sources.nixpkgs {inherit system;},
  fenix ? import sources.fenix {inherit pkgs;},
}: let
  toolchain = fenix.stable.withComponents [
    "cargo"
    "clippy"
    "rust-src"
    "rustc"
    "rustfmt"
    "rust-analyzer"
  ];
in 
  pkgs.mkShell {
    packages = [toolchain];  
  }


