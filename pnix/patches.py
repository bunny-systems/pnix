"""Resolve patch declarations to locked patch nodes.

A declaration is one of four shapes:

    patches = [
      { pr = 181; }                  # tracked to its head and base commits
      { commit = "abc123…"; }        # one commit's diff
      { url = "https://…/fix.diff"; }
      ./local.patch                  # a file in the consumer's own tree
    ];

and each resolves to a node carrying, at minimum, `url` and `hash` -- the two
fields the vendored resolver reads. Everything else (`head`, `base`, `merged`,
`state`) is provenance, there so `pnix look` can tell you a PR moved or merged
without refetching anything. That is the same split schema 2 applies to sources:
a small closed fetch discriminator, plus provenance the eval-time half never
touches.

**`.diff`, not `.patch`.** The latter is a `git format-patch` series carrying
`From <sha>` and author headers; `applyPatches` wants a plain unified diff.
Measured on finit #181: 17374 bytes against 18526.
"""

from pathlib import Path

from pnix import forges, prefetch, urls
from pnix.forges.base import ForgeError

KINDS = ("pr", "commit", "url", "path")


class PatchError(Exception):
    pass


#: a patch may name its own source repo, overriding the pin's
#: what a patch entry may say about *where* its PR lives. `repo` is a URL,
#: like a pin's; `forge` names the software when the host is unknown.
SOURCE_KEYS = ("repo", "forge")


def _source(entry: dict, spec: dict, name: str) -> dict:
    """Where this patch comes from: the pin's repo unless the patch says otherwise.

    A patch does not have to live in the repo it applies to. The case that forced
    this: a pin fetched from a self-hosted Forgejo mirror, whose pull requests are
    upstream on GitHub. The patch names the repo the PR lives in, as a URL, the
    same way a pin does:

        patches = [ { pr = 42; repo = "https://github.com/rasmus-kirk/nixarr"; } ];

    `forge` is only needed when that host is not one pnix recognises -- a
    self-hosted instance, where no table can know what software runs there.
    """
    url = entry.get("repo") or spec.get("url")
    if not url:
        raise PatchError(
            f"pin '{name}': this patch needs a repo to look the pull request up "
            f"in. The pin has no url, so give the patch one: "
            f'`repo = "https://host/owner/name";`'
        )
    try:
        host, owner, repo = urls.parse(url)
    except urls.UrlError as err:
        raise PatchError(f"pin '{name}': patch repo: {err}") from None

    out = {"host": host, "owner": owner, "repo": repo}
    forge = entry.get("forge") or spec.get("forge")
    if forge:
        out["forge"] = forge
    return out


def _forge_for(spec: dict, name: str) -> tuple[str, str]:
    """(forge name, host) for a PR-shaped patch."""
    host = spec.get("host") or "github.com"
    forge = spec.get("forge") or forges.for_host(host)
    if forge is None:
        raise PatchError(
            f"pin '{name}': cannot tell which forge {host!r} runs. "
            f"Add `forge = \"github\";` or `forge = \"forgejo\";` -- to the "
            f"patch if it lives on a different host than the pin."
        )
    return forge, host


def _relative(path: str, project: Path, name: str) -> str:
    """A local patch is recorded relative to the project root.

    An absolute path would not survive a clone onto another machine, and the
    lock is committed. Refusing is better than writing one down.
    """
    p = Path(path)
    try:
        return p.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        raise PatchError(
            f"pin '{name}': local patch {path} is outside the project root "
            f"{project}. A committed lock cannot reference it."
        ) from None


def resolve_one(entry, spec: dict, name: str, project: Path) -> dict:
    """One declaration -> one locked patch node. Downloads."""
    if isinstance(entry, str):
        rel = _relative(entry, project, name)
        if not (project / rel).is_file():
            raise PatchError(f"pin '{name}': no such patch file: {rel}")
        return {"kind": "path", "path": rel}

    if not isinstance(entry, dict):
        raise PatchError(
            f"pin '{name}': a patch must be an attrset or a path, got "
            f"{type(entry).__name__}"
        )

    if "pr" in entry:
        src = _source(entry, spec, name)
        forge_name, host = _forge_for(src, name)
        forge = forges.get(forge_name)
        number = entry["pr"]
        try:
            pull = forge.pull(host, src["owner"], src["repo"], number)
        except ForgeError as err:
            raise PatchError(f"pin '{name}': PR #{number}: {err}") from err
        url = forge.pull_diff_url(host, src["owner"], src["repo"], number, pull)
        # The tree the diff is actually generated against. Best-effort: a forge
        # without a verified endpoint returns None, and a rate-limited request
        # must not fail the whole update.
        try:
            merge_base = forge.merge_base(host, src["owner"], src["repo"],
                                          pull.base, pull.head)
        except ForgeError:
            merge_base = None
        return {
            "kind": "pr",
            "forge": forge_name,
            "host": host,
            "owner": src["owner"],
            "repo": src["repo"],
            "number": number,
            "head": pull.head,
            "base": pull.base,
            "state": pull.state,
            "merged": pull.merged,
            # Recorded because it changes what drift means: where a forge has no
            # compare endpoint this URL moves with the PR, and the hash below is
            # the only thing holding the content still.
            "immutable": forge.immutable_diffs,
            **({"mergeBase": merge_base} if merge_base else {}),
            "url": url,
            "hash": prefetch.file(url),
        }

    if "commit" in entry:
        src = _source(entry, spec, name)
        forge_name, host = _forge_for(src, name)
        forge = forges.get(forge_name)
        rev = entry["commit"]
        url = forge.commit_diff_url(host, src["owner"], src["repo"], rev)
        return {"kind": "commit", "forge": forge_name, "host": host,
                "owner": src["owner"], "repo": src["repo"], "rev": rev,
                "immutable": True, "url": url, "hash": prefetch.file(url)}

    if "url" in entry:
        url = entry["url"]
        return {"kind": "url", "immutable": True, "url": url,
                "hash": prefetch.file(url)}

    keys = ", ".join(sorted(entry)) or "(none)"
    raise PatchError(
        f"pin '{name}': a patch needs one of `pr`, `commit` or `url`, or to be "
        f"a path. Got: {keys}"
    )


def resolve(spec: dict, name: str, project: Path) -> list[dict]:
    return [resolve_one(e, spec, name, project) for e in spec.get("patches", [])]


def advice(node: dict) -> list[str]:
    """What the lock alone already tells you. No network.

    A patch tracking a merged PR is actionable whether it merged yesterday or
    before you ever locked it -- it is carrying a diff upstream already has.
    Reporting it only as *drift* would mean a pin locked after the merge stays
    quiet forever, which is the case most likely to be forgotten.
    """
    out: list[str] = []
    for patch in node.get("patches", []):
        if patch.get("kind") != "pr":
            continue
        if patch.get("merged"):
            out.append(
                f"PR #{patch['number']} is merged upstream; bump the pin's rev "
                f"and drop the patch"
            )
        elif patch.get("state") == "closed":
            out.append(f"PR #{patch['number']} was closed without merging")
    return out


def _node_source(node: dict) -> tuple[str, str, str]:
    """(host, owner, repo) for a locked node.

    Prefers the recorded provenance and falls back to parsing the node's url,
    so a node written before those fields were derived still reports drift
    rather than raising KeyError.
    """
    host = node.get("host")
    owner, repo = node.get("owner"), node.get("repo")
    if not (host and owner and repo) and isinstance(node.get("url"), str):
        try:
            u_host, u_owner, u_repo = urls.parse(node["url"])
        except urls.UrlError:
            pass
        else:
            host, owner, repo = host or u_host, owner or u_owner, repo or u_repo
    return host or "github.com", owner, repo


def drift(node: dict) -> list[str]:
    """What changed upstream since this patch was locked. One request per PR.

    The part nothing else has, and nearly free: `head` and `base` were stored at
    lock time precisely so this needs no diff and no clone.
    """
    out: list[str] = []
    for patch in node.get("patches", []):
        if patch.get("kind") != "pr":
            continue
        label = f"PR #{patch['number']}"
        try:
            forge = forges.get(patch["forge"])
            # The patch records its own repo, which is not necessarily the
            # pin's -- a mirrored pin can track an upstream PR.
            host, owner, repo = _node_source(node)
            pull = forge.pull(patch.get("host") or host,
                              patch.get("owner") or owner,
                              patch.get("repo") or repo,
                              patch["number"])
        except (ForgeError, KeyError, forges.UnknownForge) as err:
            out.append(f"{label}: could not check ({err})")
            continue
        if pull.merged and not patch.get("merged"):
            out.append(f"{label} has been merged since you locked it")
        elif pull.state == "closed" and not pull.merged and not patch.get("merged"):
            out.append(f"{label} has been closed without merging since you locked it")
        if pull.head != patch["head"]:
            out.append(
                f"{label} has new commits since you locked "
                f"({patch['head'][:8]} -> {pull.head[:8]})"
            )
        if pull.base != patch["base"]:
            out.append(f"{label} was rebased onto a new base "
                       f"({patch['base'][:8]} -> {pull.base[:8]})")
    return out


def applies_to(node: dict) -> list[str]:
    """Warnings about patches that will not apply to the rev they are pinned to.

    A forge renders a PR diff from the **merge base** of base and head, not from
    the base commit and certainly not from whatever rev the pin names. When
    those differ the patch may still apply by luck, or fuzz, or fail -- and pnix
    forbids fuzz, so the failure lands at build time. Saying so at lock time
    turns a confusing build error into a sentence.
    """
    out: list[str] = []
    rev = node.get("rev")
    for patch in node.get("patches", []):
        base = patch.get("mergeBase")
        if patch.get("kind") != "pr" or not base or not rev or base == rev:
            continue
        out.append(
            f"PR #{patch['number']}: its diff is generated against "
            f"{base[:8]}, but this pin is at {rev[:8]}. The patch may not "
            f"apply; pnix forbids fuzz, so a mismatch fails the build."
        )
    return out
