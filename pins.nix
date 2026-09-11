# pnix pins its own dev environment, with pnix.
#
# `flake = false`: the dev shell wants nixpkgs as a *tree* to `import`, not as
# an evaluated flake, and skipping the flake evaluation is both simpler and
# faster. It also means this repo's flake has no inputs at all.
#
# No `type`: github.com is a host pnix knows, so the url settles it.
{
  pins.nixpkgs = {
    url = "https://github.com/NixOS/nixpkgs";
    ref = "nixos-unstable";
    flake = false;
  };
}
