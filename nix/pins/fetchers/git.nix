# pnix-managed. delete this line to take ownership; pnix will leave it alone.
# A git checkout. Content-addressed by rev, so no hash is stored: a rev is
# already a cryptographic commitment to the tree.
#
# The primitive to use when submodules are needed -- forge tarballs do not
# carry them. Without a `ref`, allRefs is required: fetchGit defaults to the
# remote's HEAD branch and cannot find a rev that lives anywhere else.
f:
builtins.fetchGit (
  {
    inherit (f) url rev;
  }
  // (if f ? ref then { inherit (f) ref; } else { allRefs = true; })
  // (if f.submodules or false then { submodules = true; } else { })
  // (if f.shallow or false then { shallow = true; } else { })
)
