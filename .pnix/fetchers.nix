# pnix-managed. delete this line to take ownership; pnix will leave it alone.
{ }:
let
  primitives = {
    tarball =
      f:
      builtins.fetchTarball {
        inherit (f) url;
        sha256 = f.hash;
      };

    file =
      f:
      builtins.fetchurl {
        inherit (f) url;
        sha256 = f.hash;
      };

    git =
      f:
      builtins.fetchGit (
        {
          inherit (f) url rev;
        }
        // (if f ? ref then { inherit (f) ref; } else { allRefs = true; })
        // (if f.submodules or false then { submodules = true; } else { })
        // (if f.shallow or false then { shallow = true; } else { })
      );

    path = f: /. + f.path;
  };

  known = names: builtins.concatStringsSep ", " (builtins.attrNames names);

  byType = { };
in
primitives
// {
  inherit primitives byType;

  fetch =
    node:
    if node ? fetch then
      let
        k = node.fetch.kind or (throw "pnix: lock node has a `fetch` with no `kind`");
        f =
          primitives.${k} or (throw
            "pnix: no fetcher for kind '${k}'; known: ${known primitives}. A newer pnix wrote this lock -- re-run `pnix init` to update the vendored resolver."
          );
      in
      f node.fetch
    else
      let
        t = node.type or (throw "pnix: lock node has neither `fetch` nor `type`");
        f =
          byType.${t} or (throw
            "pnix: no fetcher for type '${t}'; known kinds: ${known primitives}. Re-run `pnix update`, which writes the `fetch` discriminator this resolver reads."
          );
      in
      f node;
}
