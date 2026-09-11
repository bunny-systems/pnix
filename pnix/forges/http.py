"""Minimal JSON-over-HTTP, stdlib only.

No token handling. Every forge endpoint pnix touches is public for the pins it
was built for, and an unauthenticated GitHub client gets 60 requests an hour --
enough for a handful of patches, and a hard ceiling worth knowing about. It is
also why ref resolution stays on `git ls-remote`, which has no such limit.
"""

import json
import urllib.error
import urllib.request

from pnix.forges.base import ForgeError

USER_AGENT = "pnix"
TIMEOUT = 45


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as err:
        detail = ""
        if err.code in (403, 429):
            detail = (" -- this is the unauthenticated rate limit; pnix uses no "
                      "token, so wait or lock fewer PR patches at once")
        raise ForgeError(f"{url}: HTTP {err.code}{detail}") from err
    except OSError as err:
        raise ForgeError(f"{url}: {err}") from err
    except json.JSONDecodeError as err:
        raise ForgeError(f"{url}: not JSON: {err}") from err
