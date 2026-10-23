# pnix-managed. delete this line to take ownership; pnix will leave it alone.
# SPDX-License-Identifier: EUPL-1.2
{
  pins,
  var ? "PNIX_OVERRIDE",
}:
let
  raw = builtins.getEnv var;

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
