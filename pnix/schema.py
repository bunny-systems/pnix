"""Validate collected declarations.

Stands in for the Nix option types pnix deliberately does not use: option
types live in nixpkgs' lib, and the collector is pure builtins so it can run
before any nixpkgs is fetched. Validating here also means a typo is reported
with the file that contains it, which is what a user actually needs.
"""

from pnix import urls

FIELDS: dict[str, type] = {
    "type": str,
    "url": str,
    "path": str,
    "ref": str,
    "rev": str,
    "tag": str,
    "release": str,
    "dir": str,
    "submodules": bool,
    "shallow": bool,
    "lfs": bool,
    "exportIgnore": bool,
    "flake": bool,
    "follows": dict,
    "excludeFollow": list,
    "patches": list,
    "importable": bool,
    "forge": str,
    "channel": str,
}

# Fields the schema knows about but nothing acts on yet. Accepting one silently
# is worse than rejecting it: a declared field carried into the lock and read by
# nothing means believing something is true of an input when it is not. `dir`
# and `patches` were both in this state until they were implemented.
NOT_IMPLEMENTED: dict[str, str] = {}

# A patch declaration is one of these keys, or a bare path.
PATCH_KEYS = ("pr", "commit", "url")

FORGE_NAMES = ("github", "forgejo", "gitea")

REQUIRED: dict[str, tuple[str, ...]] = {
    "github": ("url",),
    "forgejo": ("url",),
    "gitea": ("url",),
    "gitlab": ("url",),
    "sourcehut": ("url",),
    "git": ("url",),
    "tarball": ("url",),
    "file": ("url",),
    "channel": ("channel",),
    "path": ("path",),
}

# Source types with no PR API in pnix.forges, so `{ pr = N; }` cannot be
# resolved against them. Refusing beats guessing an endpoint shape.
NO_PR_API = ("gitlab", "sourcehut", "git", "tarball", "file", "channel", "path")

# Options fetchGit accepts and a forge archive cannot honour. Asking for one
# on a tarball pin is not a warning: the pin would fetch, evaluate, and be
# quietly missing the submodule.
GIT_ONLY = ("submodules", "shallow", "lfs", "exportIgnore")


def type_of(spec: dict) -> str | None:
    """The source type for a declaration: stated, or read off the URL's host.

    None means neither worked -- an unknown host with no `type`. Guessing which
    software a self-hosted domain runs is how you get a 404 at lock time.
    """
    stated = spec.get("type")
    if stated:
        return stated
    url = spec.get("url")
    return urls.infer_type(url) if isinstance(url, str) else None


class SchemaError(Exception):
    pass


def validate(pins: dict, provenance: dict) -> None:
    problems: list[str] = []

    for name in sorted(pins):
        spec = pins[name]
        where = provenance.get(name, "<unknown file>")

        if not isinstance(spec, dict):
            problems.append(
                f"{where}: pin '{name}' should be an attrset, got "
                f"{type(spec).__name__}"
            )
            continue

        for field, value in sorted(spec.items()):
            expected = FIELDS.get(field)
            if expected is None:
                near = ", ".join(sorted(FIELDS))
                problems.append(
                    f"{where}: pin '{name}' has unknown field '{field}'. "
                    f"known fields: {near}"
                )
            elif not isinstance(value, expected):
                problems.append(
                    f"{where}: pin '{name}' field '{field}' should be "
                    f"{expected.__name__}, got {type(value).__name__}"
                )

        for field, why in sorted(NOT_IMPLEMENTED.items()):
            if field in spec:
                problems.append(
                    f"{where}: pin '{name}' sets '{field}', which pnix accepts "
                    f"but does not act on ({why}). Refusing rather than "
                    f"silently ignoring it."
                )

        chosen = [k for k in ("tag", "release", "ref") if spec.get(k)]
        if len(chosen) > 1:
            problems.append(
                f"{where}: pin '{name}' sets {', '.join(chosen)}; these are "
                f"alternative ways to choose a revision, so pick one. "
                f"(`rev` may accompany any of them: it pins exactly while the "
                f"other records what was being tracked.)"
            )

        problems.extend(_patch_problems(name, spec, where))

        kind = type_of(spec)
        if kind is None:
            known = ", ".join(sorted(set(urls.HOSTS.values())))
            problems.append(
                f"{where}: pin '{name}' has no `type` and its host is not one "
                f"pnix knows, so it cannot tell what runs there. Add `type`: "
                f"{known}, or `git` for a plain clone."
            )
            continue
        if kind not in REQUIRED:
            problems.append(
                f"{where}: pin '{name}' has unknown type '{kind}'. "
                f"known types: {', '.join(sorted(REQUIRED))}"
            )
            continue

        for field in REQUIRED.get(kind, ()):
            if not spec.get(field):
                problems.append(
                    f"{where}: pin '{name}' is type '{kind}' and needs '{field}'"
                )

        if kind != "git":
            asked = [f for f in GIT_ONLY if spec.get(f)]
            if asked:
                problems.append(
                    f"{where}: pin '{name}' sets {', '.join(asked)}, which a "
                    f"'{kind}' archive cannot carry. Add `type = \"git\"` to "
                    f"clone it instead."
                )

        if isinstance(spec.get("url"), str) and kind in ("github", "forgejo", "gitea", "gitlab",
                                        "sourcehut", "git"):
            try:
                urls.parse(spec["url"])
            except urls.UrlError as e:
                problems.append(f"{where}: pin '{name}': {e}")

    if problems:
        raise SchemaError("\n".join(problems))


def _patch_problems(name: str, spec: dict, where: str) -> list[str]:
    out: list[str] = []
    patches = spec.get("patches")
    if patches is None:
        return out

    if spec.get("importable") and not patches:
        out.append(f"{where}: pin '{name}' sets 'importable' but has no patches")

    kind = type_of(spec) or ""
    for i, entry in enumerate(patches):
        at = f"{where}: pin '{name}' patch [{i}]"
        # A patch may name its own repo and forge, which is how a pin mirrored
        # from elsewhere tracks a PR on the upstream it was mirrored from.
        names_own_source = isinstance(entry, dict) and (
            "forge" in entry
            or (isinstance(entry.get("repo"), str)
                and urls.infer_type(entry["repo"]) not in (None, *NO_PR_API))
        )
        if (isinstance(entry, dict) and "pr" in entry and kind in NO_PR_API
                and not spec.get("forge") and not names_own_source):
            out.append(
                f"{at}: pin type '{kind}' has no pull-request API in pnix. "
                f"Use `commit` or `url`, or set `forge` if the host runs one "
                f"pnix knows."
            )
            continue
        if isinstance(entry, str):
            continue                      # a path, checked when it is resolved
        if not isinstance(entry, dict):
            out.append(f"{at}: must be an attrset or a path, got "
                       f"{type(entry).__name__}")
            continue
        for key in ("repo", "forge"):
            if key in entry and not isinstance(entry[key], str):
                out.append(f"{at}: '{key}' must be a string")
        for gone in ("owner", "host"):
            if gone in entry:
                out.append(
                    f"{at}: '{gone}' is not a patch field. Name the repo the "
                    f'pull request lives in as a url: `repo = '
                    f'"https://host/owner/name";`'
                )
        if isinstance(entry.get("repo"), str):
            try:
                urls.parse(entry["repo"])
            except urls.UrlError as e:
                out.append(f"{at}: {e}")
        present = [k for k in PATCH_KEYS if k in entry]
        if len(present) != 1:
            out.append(
                f"{at}: needs exactly one of {', '.join(PATCH_KEYS)}; "
                f"got {', '.join(present) or 'none'}"
            )
            continue
        if "pr" in entry and not isinstance(entry["pr"], int):
            out.append(f"{at}: 'pr' must be a number")
        for key in ("commit", "url"):
            if key in entry and not isinstance(entry[key], str):
                out.append(f"{at}: '{key}' must be a string")
    return out
