from pnix.forges.base import Forge, ForgeError, Pull, UnknownForge
from pnix.forges.forgejo import Forgejo
from pnix.forges.github import GitHub

__all__ = ["ALIASES", "BY_HOST", "FORGES", "Forge", "ForgeError", "Pull",
           "UnknownForge", "for_host", "get"]

FORGES: dict[str, Forge] = {f.name: f for f in (GitHub(), Forgejo())}

# Aliases for forges that are the same API under another name.
ALIASES = {"gitea": "forgejo"}

# Hosts whose forge is known without being told.
BY_HOST = {
    "github.com": "github",
    "codeberg.org": "forgejo",
}


def get(name: str) -> Forge:
    name = ALIASES.get(name, name)
    try:
        return FORGES[name]
    except KeyError:
        known = ", ".join(sorted(set(FORGES) | set(ALIASES)))
        raise UnknownForge(
            f"unknown forge {name!r}; known: {known}. "
            f"GitLab is deliberately absent: its endpoints were never probed, "
            f"and the Forgejo row that sat beside it in the design for weeks "
            f"turned out to be wrong."
        ) from None


def for_host(host: str) -> str | None:
    """The forge a host runs, when that can be told from the name alone."""
    return BY_HOST.get(host)
