# pnix-managed. delete this line to take ownership; pnix will leave it alone.
{ }:
let
  rest = f: removeAttrs f [ "kind" "hash" ];

  primitives = {
    tarball = f: builtins.fetchTarball (rest f // { sha256 = f.hash; });

    file = f: builtins.fetchurl (rest f // { sha256 = f.hash; });

    git = f: builtins.fetchGit (rest f // (if f ? ref then { } else { allRefs = true; }));

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
