{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs";
  inputs.helper.follows = "nixpkgs";
  inputs.nested = {
    url = "github:o/r";
    inputs.nixpkgs.follows = "nixpkgs";
  };
  outputs = { self, nixpkgs, ... }: { value = "built"; };
}
