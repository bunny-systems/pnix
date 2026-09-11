# pnix-managed. delete this line to take ownership; pnix will leave it alone.
# SPDX-License-Identifier: EUPL-1.2
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
