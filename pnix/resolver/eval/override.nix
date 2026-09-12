# Imperative, per-run input overrides from the environment.
#
#   PNIX_OVERRIDE=finix=/home/nimeses/Projects/nix/finix nixos-rebuild …
#   PNIX_OVERRIDE="finix=~/Projects/nix/finix,hjem=/tmp/hjem" nix-instantiate …
#
# Entries are `name=path`, separated by whitespace or commas. The path replaces
# whatever the lock says for that pin; everything downstream is unchanged, so a
# local checkout containing a flake.nix is evaluated as a flake exactly as a
# fetched one would be.
#
# A value is either an **absolute path** -- a working tree -- or a **repo URL**,
# optionally `#<ref-or-rev>`:
#
#   PNIX_OVERRIDE=finix=/home/me/Projects/finix
#   PNIX_OVERRIDE=finix=https://github.com/finix-community/finix
#   PNIX_OVERRIDE=finix=https://github.com/finix-community/finix#refs/pull/181/head
#   PNIX_OVERRIDE=finix=https://github.com/finix-community/finix#34156d814e6d…
#
# This file used to refuse a URL, on the grounds that tack resolves one with
# `builtins.getFlake` -- an experimental feature pnix cannot use. That confused
# *tack's implementation* needing flakes with *the feature* needing them:
# `fetchGit` resolves a ref or a rev with no hash and no experimental feature,
# and the override only ever runs in impure evaluation anyway, since it is read
# from `getEnv`.
#
# The line an override draws is **transient versus recorded**, not local versus
# remote. Nothing here reaches the lock, whichever form is used; a source you
# keep reaching for belongs in a declaration, which is equally true of a path.
#
# `#` separates the ref because it cannot appear in a git URL, where `@` can
# (`git@github.com:o/r`). No `gh:` shorthand: that is the flake mini-language
# the declaration syntax deliberately does not use.
#
# `~/` is expanded, which the design argues against for *path pins* because
# those are relative to their declaring file. An environment variable has no
# declaring file, and a user typing this on a command line means their home.
#
# Every failure throws rather than being ignored: an override that silently did
# nothing would be the worst possible outcome for a variable whose entire
# purpose is "use my working tree instead".
{
  pins,
  var ? "PNIX_OVERRIDE",
}:
let
  raw = builtins.getEnv var;

  # A near-miss name is invisible to the mechanism -- pnix cannot warn about a
  # variable it does not read -- so it is looked for deliberately. `OVERRIDES`
  # is tack's spelling, and muscle memory outlives a migration: measured on a
  # real one, where the plural produced a completely normal-looking build off
  # the locked rev with no trace at all.
  nearMiss = builtins.filter (n: builtins.getEnv n != "") [
    "PNIX_OVERRIDES"
    "TACK_OVERRIDES"
  ];

  entries = builtins.filter (s: builtins.isString s && s != "") (builtins.split "[[:space:],]+" raw);

  home = builtins.getEnv "HOME";

  expand =
    s:
    if builtins.substring 0 2 s == "~/" then
      if home == "" then
        throw "${var}: cannot expand '~' because HOME is not set"
      else
        home + builtins.substring 1 (builtins.stringLength s) s
    else
      s;

  known = builtins.concatStringsSep " " (builtins.attrNames pins);

  isUrl =
    s: builtins.match "[a-z][a-z0-9+.-]*://.*" s != null || builtins.match "[^/]+@[^/]+:.*" s != null;

  # A rev needs `allRefs`, since fetchGit otherwise looks only at the remote's
  # default branch -- and the interesting revs (a PR head, someone's fork) are
  # exactly the ones that are not on it. Verified against an open PR head.
  fetchRef =
    url: frag:
    builtins.fetchGit (
      {
        inherit url;
      }
      // (
        if frag == null then
          { }
        else if builtins.match "[0-9a-f]{7,40}" frag != null then
          {
            rev = frag;
            allRefs = true;
          }
        else
          { ref = frag; }
      )
    );

  fetchUrl =
    entry: src:
    let
      m = builtins.match "([^#]+)#(.+)" src;
      url = if m == null then src else builtins.head m;
      frag = if m == null then null else builtins.elemAt m 1;
      got = fetchRef url frag;
    in
    got.outPath;

  parse =
    entry:
    let
      m = builtins.match "([^=]+)=(.+)" entry;
      fail = msg: throw "${var}: ${entry}: ${msg}";
    in
    if m == null then
      fail "not of the form pin=/absolute/path"
    else
      let
        name = builtins.head m;
        src = expand (builtins.elemAt m 1);
      in
      if !(pins ? ${name}) then
        fail "'${name}' is not a pin. known pins: ${known}"
      else if isUrl src then
        {
          inherit name;
          value = fetchUrl entry src;
        }
      else if builtins.substring 0 1 src != "/" then
        fail "'${src}' is neither an absolute path nor a repository URL. Use /abs/path, or https://host/owner/repo#<ref-or-rev>."
      else if !builtins.pathExists src then
        fail "no such directory: ${src}"
      else
        {
          inherit name;
          value = /. + src;
        };

  parsed = builtins.listToAttrs (map parse entries);
in
if raw == "" && nearMiss != [ ] then
  throw "pnix: ${builtins.head nearMiss} is set, but pnix reads ${var} (no trailing S). Rename it, or unset it if you did not mean to override anything."
else if parsed == { } then
  parsed
else
  builtins.trace "pnix: overriding inputs from ${var}: ${builtins.concatStringsSep ", " (builtins.attrNames parsed)}" parsed
