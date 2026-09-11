# pnix pins its own dev environment, with pnix.
#
# `flake = false`: the dev shell wants nixpkgs as a *tree* to `import`, not as
# an evaluated flake, and skipping the flake evaluation is both simpler and
# faster. It also means this repo's flake has no inputs at all.
{
  pins.nixpkgs = {
    owner = "NixOS";
    repo = "nixpkgs";
    ref = "nixos-unstable";
    flake = false;
  };
}
