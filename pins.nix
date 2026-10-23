{
  pins = {
    nixpkgs = {
      url = "https://github.com/NixOS/nixpkgs";
      ref = "nixos-unstable";
      flake = false;
    };

    fenix = {
      url = "https://github.com/nix-community/fenix";
      follows = {nixpkgs = "nixpkgs";};
    };
  };
}
