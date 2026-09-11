"""pnix — Nix-native input pinning."""

__version__ = "0.1.0"

#: Sent on every HTTP request pnix makes.
#:
#: Not cosmetic. Forge instances commonly block `Python-urllib/*` as a scraper,
#: and one bare `urlopen` in `prefetch.tarball` was enough to make every fetch
#: from forgejo.nimeses.com fail with `HTTP 403: Forbidden` while the same URL
#: served fine to curl and to Nix. Identifying the tool is also the polite
#: thing to do when it is hitting someone's self-hosted server.
USER_AGENT = f"pnix/{__version__}"
