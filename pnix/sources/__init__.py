from pnix.sources.base import Source, UnknownSource
from pnix.sources.channel import Channel
from pnix.sources.forgejo import Forgejo, Gitea
from pnix.sources.git import Git
from pnix.sources.github import GitHub
from pnix.sources.gitlab import GitLab
from pnix.sources.path import LocalPath
from pnix.sources.sourcehut import SourceHut
from pnix.sources.url import File, Tarball

__all__ = ["SOURCES", "Source", "UnknownSource", "get"]

SOURCES: dict[str, Source] = {
    s.type: s
    for s in (
        GitHub(), Forgejo(), Gitea(), GitLab(), SourceHut(),
        Git(), Tarball(), File(), Channel(), LocalPath(),
    )
}


def get(type: str) -> Source:
    try:
        return SOURCES[type]
    except KeyError:
        known = ", ".join(sorted(SOURCES))
        raise UnknownSource(f"unknown source type {type!r}; known: {known}") from None
