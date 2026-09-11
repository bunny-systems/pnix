# An archive at a URL, unpacked. The hash is the NAR hash of the unpacked tree,
# so upstream recompression cannot invalidate it.
#
# This is what every forge's codeload endpoint reduces to -- github, gitlab,
# forgejo/gitea, sourcehut -- which is why those are Python-only additions and
# need no new file here.
f:
builtins.fetchTarball {
  inherit (f) url;
  sha256 = f.hash;
}
