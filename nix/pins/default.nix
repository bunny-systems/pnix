# pnix-managed. delete this line to take ownership; pnix will leave it alone.
# `import ./nix/pins { }` from the consumer's own default.nix.
args: import ./resolve.nix args
