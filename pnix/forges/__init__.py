from pnix.forges.base import Forge, ForgeError, Pull, UnknownForge
from pnix.forges.forgejo import Forgejo
from pnix.forges.github import GitHub

__all__ = ["ALIASES", "BY_HOST", "FORGES", "Forge", "ForgeError", "Pull",
           "UnknownForge", "for_host", "get"]

FORGES: dict[str, Forge] = {f.name: f for f in (GitHub(), Forgejo())}

# Aliases for forges that are the same API under another name.
ALIASES = {"gitea": "forgejo"}

# Hosts whose forge is known without being told. **Derived, not a third list.**
# `urls.HOSTS` says which software a host runs and each source class says which
# PR client that software has, so writing the pairs out again meant a host could
# be added in one place and silently have no PR support in the other.
# A type with `forge = None` (gitlab, sourcehut) simply does not appear.
def _by_host() -> dict[str, str]:
    from pnix import sources, urls

    out = {}
    for host, type_name in urls.HOSTS.items():
        forge = getattr(sources.get(type_name), "forge", None)
        if forge:
            out[host] = forge
    return out


BY_HOST = _by_host()


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
