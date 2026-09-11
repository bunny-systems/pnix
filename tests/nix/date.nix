{ }:
let
  date = import ../../pnix/resolver/eval/date.nix;
in
[
  {
    name = "the epoch itself";
    expr = date 0;
    expected = "19700101000000";
  }
  {
    name = "a real pin timestamp (nixpkgs)";
    expr = date 1789050244;
    expected = "20260910142404";
  }
  {
    name = "a real pin timestamp (sops-nix)";
    expr = date 1788914643;
    expected = "20260909004403";
  }
  {
    name = "just before midnight";
    expr = date 1788911999;
    expected = "20260908235959";
  }
  {
    name = "a leap day";
    expr = date 1709208000;
    expected = "20240229120000";
  }
  {
    name = "the day after a leap day";
    expr = date 1709294400;
    expected = "20240301120000";
  }
  {
    name = "a century non-leap year boundary";
    expr = date 4102444800;
    expected = "21000101000000";
  }
  {
    name = "the first 8 chars are what nixpkgs slices";
    expr = builtins.substring 0 8 (date 1789050244);
    expected = "20260910";
  }
]
