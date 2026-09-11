# A single file at a URL, not unpacked. Note the hash is the *flat* file hash,
# not a NAR hash -- that is the whole reason this is a separate primitive from
# `tarball` rather than a flag on it.
f:
builtins.fetchurl {
  inherit (f) url;
  sha256 = f.hash;
}
