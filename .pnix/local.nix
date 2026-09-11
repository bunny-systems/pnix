# pnix-managed. delete this line to take ownership; pnix will leave it alone.
{
  pins,
  file,
}:
let
  raw = if file != null && builtins.pathExists file then import file else { };

  known = builtins.concatStringsSep " " (builtins.attrNames pins);

  unknown = builtins.filter (n: !(pins ? ${n})) (builtins.attrNames raw);

  check =
    name: value:
    if builtins.isString value then
      if builtins.substring 0 1 value != "/" then
        throw "${toString file}: '${name}' must be a path or an absolute path string, got '${value}'"
      else if !builtins.pathExists value then
        throw "${toString file}: '${name}': no such directory: ${value}"
      else
        /. + value
    else
      value;

  parsed =
    if unknown != [ ] then
      throw "${toString file}: ${builtins.concatStringsSep ", " unknown} ${if builtins.length unknown == 1 then "is not a pin" else "are not pins"}. known pins: ${known}"
    else
      builtins.mapAttrs check raw;
in
if parsed == { } then
  parsed
else
  builtins.trace "pnix: overriding inputs from ${builtins.baseNameOf (toString file)}: ${builtins.concatStringsSep ", " (builtins.attrNames parsed)}" parsed
