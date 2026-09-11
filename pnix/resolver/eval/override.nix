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
# **Absolute paths only.** tack accepts flake refs here too, but it resolves
# them with `builtins.getFlake` (override.nix:42,49) -- an experimental feature
# pnix cannot use. A remote ref is what the lock is for; this is the escape
# hatch for a working tree.
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
      else if builtins.substring 0 1 src != "/" then
        fail "'${src}' is not an absolute path. A remote ref is what the lock is for; this overrides with a working tree."
      else if !builtins.pathExists src then
        fail "no such directory: ${src}"
      else
        {
          inherit name;
          value = /. + src;
        };

  parsed = builtins.listToAttrs (map parse entries);
in
if parsed == { } then
  parsed
else
  builtins.trace "pnix: overriding inputs from ${var}: ${builtins.concatStringsSep ", " (builtins.attrNames parsed)}" parsed
