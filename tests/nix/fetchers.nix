{ }:
let
  fetchers = import ../../pnix/resolver/eval/fetchers.nix { };
  throws = v: !(builtins.tryEval v).success;
in
[
  {
    name = "the tarball primitive exists";
    expr = builtins.isFunction fetchers.tarball;
    expected = true;
  }
  {
    name = "the git primitive exists";
    expr = builtins.isFunction fetchers.git;
    expected = true;
  }
  {
    name = "the file and path primitives exist";
    expr = builtins.isFunction fetchers.file && builtins.isFunction fetchers.path;
    expected = true;
  }
  {
    name = "a path pin needs no fetching at all";
    expr = fetchers.fetch { fetch = { kind = "path"; path = "/tmp"; }; };
    expected = /tmp;
  }
  {
    name = "an unknown kind throws and says to re-init";
    expr = throws (fetchers.fetch { fetch.kind = "mercurial"; });
    expected = true;
  }
  {
    name = "a fetch block with no kind throws";
    expr = throws (fetchers.fetch { fetch = { url = "u"; }; });
    expected = true;
  }
  {
    name = "a node with neither fetch nor type throws";
    expr = throws (fetchers.fetch { rev = "abc"; });
    expected = true;
  }
  {
    name = "a node with a type but no fetch says to re-run pnix update";
    expr = throws (fetchers.fetch { type = "github"; rev = "abc"; });
    expected = true;
  }
  {
    name = "byType is the escape hatch, and is empty on purpose";
    expr = fetchers.byType;
    expected = { };
  }
]
