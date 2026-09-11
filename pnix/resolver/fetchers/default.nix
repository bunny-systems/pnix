# Fetch dispatch.
#
# **Primitives, not source types.** A lock node carries a small closed `fetch`
# discriminator -- `{ kind = "tarball"; url; hash; }` and so on -- and this file
# dispatches on `kind`. The node's `type`, `owner`, `repo`, `host` and `ref`
# survive as provenance for `pnix look` and for humans, and nothing here reads
# them.
#
# Why: every remaining source type in the roadmap (gitlab, forgejo, gitea,
# sourcehut, tarball, file, channel, pypi, crates) reduces to one of four
# primitives. Keyed on `type`, each of those is a new .nix file in **every**
# consumer's repo and a re-run of `pnix init` on every host. Keyed on `kind`,
# each is a pure Python change and the vendored half never moves. That is the
# same narrowing the design's `## Shipping` section argues for, applied to the
# fetch layer.
#
# **Nothing is foreclosed.** A source type that genuinely does not reduce to a
# primitive can still ship its own fetcher and omit `fetch`: dispatch falls back
# to `byType`. The known candidate is a Nix channel's `nixexprs.tar.zst`, which
# `fetchTarball` cannot unpack and which tack handles with a
# `builtin:unpack-channel` derivation. Adding that later costs one re-init --
# once, for the primitive, rather than once per source type forever.
#
# Every expression reachable from here must evaluate with experimental features
# off: fetchTarball, fetchGit and fetchurl are stable; fetchTree is not.
{ }:
let
  primitives = {
    tarball = import ./tarball.nix;
    file = import ./file.nix;
    git = import ./git.nix;
    path = import ./path.nix;
  };

  known = names: builtins.concatStringsSep ", " (builtins.attrNames names);

  # Escape hatch: per-source-type fetchers, for anything that will not reduce
  # to a primitive. Empty today, and that is the point.
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
