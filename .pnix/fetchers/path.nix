# pnix-managed. delete this line to take ownership; pnix will leave it alone.
# A literal path. Carries no hash and breaks a clean clone elsewhere, so it
# belongs in pins.local.nix or an override, never in a committed lock.
f: /. + f.path
