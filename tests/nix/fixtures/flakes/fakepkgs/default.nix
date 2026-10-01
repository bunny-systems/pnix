# A stand-in nixpkgs for the resolver's patching path: enough of an
# `applyPatches` to record what it was called with, and nothing fetched.
{
  system,
  config ? { },
  overlays ? [ ],
}:
{
  applyPatches =
    args:
    let
      self = args // {
        _isDrv = true;
        inherit system;
        allowSubstitutes = false;
        overrideAttrs = f: self // f self;
      };
    in
    self;
}
