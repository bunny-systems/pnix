# pnix-managed. delete this line to take ownership; pnix will leave it alone.
# Persistent, machine-local input overrides: `<project>/.pnix/pins.local.nix`.
#
# The declarative half of the override story. PNIX_OVERRIDE is for one command;
# this is for a work session, and it survives closing the shell without being
# retyped. Gitignore it -- that is the whole point, and pnix refuses to write
# one into a repo for you.
#
#     # pins.local.nix
#     {
#       finix = ../finix;                        # a path literal, relative to here
#       hjem  = "/home/me/Projects/nix/hjem";     # or an absolute path as a string
#     }
#
# It overrides **resolution**, not declaration: the value replaces what the lock
# fetches, and nothing is re-locked. Changing a pin's rev is `pnix update`, not
# this. Consequently the file needs no `pins` attribute and must not have one --
# a `pins.<name>` in here would make it a declaration candidate and the lock
# would grow an entry nobody meant.
#
# An unknown name throws rather than being ignored, for the same reason as
# PNIX_OVERRIDE: an override that silently does nothing is the worst outcome for
# a mechanism whose only job is to take effect.
{
  pins,
  file,
}:
let
  raw = if file != null && builtins.pathExists file then import file else { };

  known = builtins.concatStringsSep " " (builtins.attrNames pins);

  # Eagerly, because `mapAttrs` is lazy in its values: a key nobody looks up is
  # a key whose check never runs, and a typo would sit in this file doing
  # nothing forever. Forcing the *names* is cheap and catches exactly that.
  # Value checks below stay lazy on purpose -- they only matter for an override
  # something actually reads.
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
