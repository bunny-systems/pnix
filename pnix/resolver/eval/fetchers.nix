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
# All four primitives live here rather than in a file each. They are two to five
# lines apiece, and a file each bought nothing except the impression that adding
# a forge means adding one -- which is exactly what the paragraph above says it
# must not mean.
#
# Every expression reachable from here must evaluate with experimental features
# off: fetchTarball, fetchGit and fetchurl are stable; fetchTree is not.
{ }:
let
  # Everything in a `fetch` node except the discriminator and the hash goes to
  # the builtin **unchanged**. That is the point: `submodules`, `shallow`,
  # `lfs`, `exportIgnore`, `name`, `verifyCommit`, `publicKeys` and whatever a
  # future Nix adds all work without touching this file, so adding one is a
  # pure Python change and the vendored half stays frozen -- the same argument
  # that made dispatch key on `kind` rather than `type`, one level further in.
  #
  # Safe because `fetch` is closed: it is built by `Source.fetch_spec`, never by
  # a user. A field the builtin does not know still fails loudly -- fetchGit
  # answers `input attribute 'x' not supported by scheme 'git'`.
  rest =
    f:
    removeAttrs f [
      "kind"
      "hash"
    ];

  primitives = {
    # An archive at a URL, unpacked. The hash is the NAR hash of the unpacked
    # tree, so upstream recompression cannot invalidate it. This is what every
    # forge's codeload endpoint reduces to -- github, gitlab, forgejo/gitea,
    # sourcehut -- which is why those are Python-only additions.
    tarball = f: builtins.fetchTarball (rest f // { sha256 = f.hash; });

    # A single file at a URL, not unpacked. The hash is the *flat* file hash,
    # not a NAR hash -- that is the whole reason this is a separate primitive
    # from `tarball` rather than a flag on it.
    file = f: builtins.fetchurl (rest f // { sha256 = f.hash; });

    # A git checkout. Content-addressed by rev, so no hash is stored: a rev is
    # already a cryptographic commitment to the tree.
    #
    # The primitive to use when submodules are needed -- forge tarballs do not
    # carry them. Without a `ref`, allRefs is required: fetchGit defaults to the
    # remote's HEAD branch and cannot find a rev that lives anywhere else.
    git = f: builtins.fetchGit (rest f // (if f ? ref then { } else { allRefs = true; }));

    # A literal path. Carries no hash and breaks a clean clone elsewhere, so it
    # belongs in an override, never in a committed lock.
    path = f: /. + f.path;
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
          primitives.${k}
            or (throw "pnix: no fetcher for kind '${k}'; known: ${known primitives}. A newer pnix wrote this lock -- re-run `pnix init` to update the vendored resolver.");
      in
      f node.fetch
    else
      let
        t = node.type or (throw "pnix: lock node has neither `fetch` nor `type`");
        f =
          byType.${t}
            or (throw "pnix: no fetcher for type '${t}'; known kinds: ${known primitives}. Re-run `pnix update`, which writes the `fetch` discriminator this resolver reads.");
      in
      f node;
}
