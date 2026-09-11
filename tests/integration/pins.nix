# The 23 pins of ~/nixconfig, transcribed by hand from .tack/pins.toml.
# In the real migration these move into the modules that use them; here
# they are one file so the gate can run without touching the config.
#
# A plain attrset with a `pins` attribute -- exactly what the collector reads.
# No module system, no lib.
#
# Two transcription notes, because tack's lock cannot be read back for them:
#   * tack stores no `ref`, so `halley` (branch feat/flake) and `noctalia`
#     (branch cachix) look default-branch in the lock and are not.
#   * `hyprland` and `umbriel` are `gh:` urls in pins.toml but land as type
#     git, because they need submodules and forge tarballs do not carry them.
{
  pins = {
    nixpkgs = {
      owner = "NixOS";
      repo = "nixpkgs";
    };
    sops-nix = {
      owner = "Mic92";
      repo = "sops-nix";
      excludeFollow = [ "nixpkgs" ];
    };
    disko = {
      owner = "nix-community";
      repo = "disko";
      excludeFollow = [ "nixpkgs" ];
    };
    finix = {
      owner = "finix-community";
      repo = "finix";
    };
    community-modules = {
      owner = "finix-community";
      repo = "community-modules";
    };
    hjem = {
      owner = "feel-co";
      repo = "hjem";
    };
    hjem-rum = {
      owner = "snugnug";
      repo = "hjem-rum";
    };
    deploy-rs = {
      owner = "serokell";
      repo = "deploy-rs";
    };
    hardware = {
      owner = "NixOS";
      repo = "nixos-hardware";
    };
    impermanence = {
      owner = "nix-community";
      repo = "impermanence";
      excludeFollow = [ "nixpkgs" ];
    };
    nix-index-database = {
      owner = "nix-community";
      repo = "nix-index-database";
      excludeFollow = [ "nixpkgs" ];
    };
    authentik-nix = {
      owner = "nix-community";
      repo = "authentik-nix";
      excludeFollow = [ "nixpkgs" ];
    };
    nix-cachyos-kernel = {
      owner = "xddxdd";
      repo = "nix-cachyos-kernel";
      excludeFollow = [ "nixpkgs" ];
    };
    glide = {
      owner = "glide-browser";
      repo = "glide.nix";
    };
    halley = {
      owner = "N1meses";
      repo = "halley";
      ref = "feat/flake";
    };
    mango = {
      owner = "mangowm";
      repo = "mango";
      excludeFollow = [ "nixpkgs" ];
    };
    noctalia = {
      owner = "noctalia-dev";
      repo = "noctalia";
      ref = "cachix";
      excludeFollow = [ "nixpkgs" ];
    };
    systems = {
      owner = "nix-systems";
      repo = "default-linux";
    };
    zed-extensions = {
      owner = "SwornSystems";
      repo = "nix-zed-extensions";
    };

    niri-nix = {
      type = "git";
      url = "https://codeberg.org/BANanaD3V/niri-nix";
      excludeFollow = [ "nixpkgs" ];
    };
    nixarr = {
      type = "git";
      url = "https://forgejo.nimeses.com/NixOS/nixarr.git";
    };
    hyprland = {
      type = "git";
      url = "https://github.com/hyprwm/Hyprland.git";
      submodules = true;
    };
    umbriel = {
      type = "git";
      url = "https://github.com/noctalia-dev/umbriel";
      submodules = true;
    };
  };
}
