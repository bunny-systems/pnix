# Recursive .nix file listing, builtins only.
#
# A convenience for small trees. The normal path is the CLI passing an
# explicit, grep-narrowed file list (pnix/discover.py): importing a whole tree
# speculatively neither scales nor is safe.
dir:
let
  go =
    d:
    let
      entries = builtins.readDir d;
    in
    builtins.concatMap (
      name:
      let
        p = d + "/${name}";
        t = entries.${name};
      in
      if t == "directory" then
        go p
      else if t == "regular" && builtins.match ".*\\.nix" name != null then
        [ p ]
      else
        [ ]
    ) (builtins.attrNames entries);
in
go dir
