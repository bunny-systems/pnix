"""Validate collected declarations.

Stands in for the Nix option types pnix deliberately does not use: option
types live in nixpkgs' lib, and the collector is pure builtins so it can run
before any nixpkgs is fetched. Validating here also means a typo is reported
with the file that contains it, which is what a user actually needs.
"""

FIELDS: dict[str, type] = {
    "type": str,
    "owner": str,
    "repo": str,
    "host": str,
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
    "github": ("owner", "repo"),
    "forgejo": ("owner", "repo"),
    "gitea": ("owner", "repo"),
    "gitlab": ("owner", "repo"),
    "sourcehut": ("owner", "repo"),
    "git": ("url",),
    "tarball": ("url",),
    "file": ("url",),
    "channel": ("channel",),
    "path": ("path",),
}

# Source types with no PR API in pnix.forges, so `{ pr = N; }` cannot be
# resolved against them. Refusing beats guessing an endpoint shape.
NO_PR_API = ("gitlab", "sourcehut", "git", "tarball", "file", "channel", "path")

DEFAULT_TYPE = "github"


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

        kind = spec.get("type", DEFAULT_TYPE)
        for field in REQUIRED.get(kind, ()):
            if not spec.get(field):
                problems.append(
                    f"{where}: pin '{name}' is type '{kind}' and needs '{field}'"
                )

    if problems:
        raise SchemaError("\n".join(problems))


def _patch_problems(name: str, spec: dict, where: str) -> list[str]:
    out: list[str] = []
    patches = spec.get("patches")
    if patches is None:
        return out

    if spec.get("importable") and not patches:
        out.append(f"{where}: pin '{name}' sets 'importable' but has no patches")

    kind = spec.get("type", DEFAULT_TYPE)
    for i, entry in enumerate(patches):
        at = f"{where}: pin '{name}' patch [{i}]"
        # A patch may name its own repo and forge, which is how a pin with no
        # owner/repo -- or one mirrored from elsewhere -- tracks a PR.
        names_own_source = isinstance(entry, dict) and "forge" in entry
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
        for key in ("owner", "repo", "host", "forge"):
            if key in entry and not isinstance(entry[key], str):
                out.append(f"{at}: '{key}' must be a string")
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
