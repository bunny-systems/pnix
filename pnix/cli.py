"""pnix command line.

update  collect declarations, resolve refs to revs, hash, write the lock
look    resolve refs without writing or downloading, report what moved
init    copy the eval-time resolver into this repo
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pnix import collect as collect_mod
from pnix import discover, refs, schema, sources, vendor
from pnix import lock as lock_mod
from pnix import patches as patches_mod

LOCK_NAME = "pins.lock.json"
ATTR = "pins"

# Declaration fields that do not affect what gets fetched.
NON_FETCH_FIELDS = {"patches", "importable", "follows", "excludeFollow", "flake",
                    "dir", "forge"}

# Declaration fields that are pnix bookkeeping but must reach the resolver.
CARRIED_FIELDS = ("importable", "follows", "excludeFollow", "flake", "dir")


def _fetch_spec(spec: dict) -> dict:
    return {k: v for k, v in spec.items() if k not in NON_FETCH_FIELDS}


def _unchanged(spec: dict, entry: dict) -> bool:
    """True when the declaration still describes the locked node."""
    if not entry:
        return False
    if "hash" not in entry and entry.get("type") != "git":
        return False
    for key, value in _fetch_spec(spec).items():
        if key == "rev":
            continue
        if entry.get(key) != value:
            return False
    return True


def _collect(project: Path, roots: list[Path] | None) -> tuple[dict, dict]:
    files = discover.candidates(roots or [project], attr=ATTR)
    pins, provenance, skipped = collect_mod.collect(files, attr=ATTR)
    if skipped:
        # Not an error: a candidate that cannot be forced with stubbed
        # arguments is nearly always a package expression that merely mentions
        # the attribute name. Say so anyway -- if a file the user expected to
        # declare pins is in here, this line is how they find out.
        print(
            f"pnix: skipped {len(skipped)} of {len(files)} candidates "
            f"(not declaration files)",
            file=sys.stderr,
        )
        for path in skipped:
            print(f"  skipped {path}", file=sys.stderr)
    schema.validate(pins, provenance)
    return pins, provenance


def _patches_changed(spec: dict, entry: dict) -> bool:
    """True when the declaration's patches no longer match the locked ones.

    Compared on the declaration, not the resolved node: a `{ pr = 181; }` whose
    upstream head moved is *drift*, reported by `look`, not a reason for
    `update` to silently refetch. Adding or removing a patch is a change.
    """
    declared = spec.get("patches", [])
    locked = entry.get("patches", [])
    if len(declared) != len(locked):
        return True
    for want, have in zip(declared, locked, strict=True):
        if isinstance(want, str):
            if have.get("kind") != "path" or not want.endswith(have.get("path", "\0")):
                return True
        elif "pr" in want and (have.get("kind") != "pr"
                               or have.get("number") != want["pr"]) or "commit" in want and (have.get("kind") != "commit"
                                   or have.get("rev") != want["commit"]) or "url" in want and (have.get("kind") != "url"
                                or have.get("url") != want["url"]):
            return True
    return False


def _resolve_all(project: Path, names: list[str], write: bool,
                 roots: list[Path] | None = None,
                 prefetch: bool = True) -> tuple[dict, dict]:
    """Resolve every declared pin. Returns (resolved, previous lock contents).

    `prefetch=False` is what makes `pnix look` cheap: resolving a ref is one
    `git ls-remote`, while hashing means downloading the source. Drift can be
    reported from the rev alone.
    """
    lock_path = project / LOCK_NAME
    existing = lock_mod.read(lock_path)
    pins, _ = _collect(project, roots)

    result: dict[str, dict] = {}
    todo: dict[str, dict] = {}
    for name, spec in pins.items():
        if names and name not in names:
            if name in existing:
                result[name] = existing[name]
            continue
        todo[name] = spec

    # Each pin is an independent network round-trip: ~0.86 s for the ls-remote
    # alone, so 21 pins serially is ~18 s. Pool the whole per-pin pipeline
    # (resolve, then prefetch when the rev actually moved).
    def one(name: str) -> tuple[str, dict]:
        spec = todo[name]
        src = sources.get(spec.get("type", schema.DEFAULT_TYPE))
        locked = src.resolve(spec)

        prior = existing.get(name, {})
        if (_unchanged(spec, prior)
                and prior.get("rev") == locked.get("rev")
                and not _patches_changed(spec, prior)):
            return name, prior

        if prefetch:
            locked.update(src.prefetch(locked))
        for key in CARRIED_FIELDS:
            if key in spec:
                locked[key] = spec[key]
        if prefetch:
            # The closed discriminator the vendored resolver reads. Computed
            # here, at lock time, so that adding a forge never touches a
            # consumer's repo.
            locked["fetch"] = src.fetch_spec(locked)
            if spec.get("patches"):
                # Resolved after `fetch` so a patch node can be compared
                # against the source it applies to when `look` reports drift.
                locked["patches"] = patches_mod.resolve(spec, name, project)
        return name, locked

    if todo:
        workers = min(refs.DEFAULT_WORKERS, len(todo))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for name, locked in pool.map(one, list(todo)):
                result[name] = locked

    if write:
        lock_mod.write(lock_path, result)
    return result, existing


def cmd_update(args) -> int:
    resolved, _ = _resolve_all(Path(args.project), args.names, write=True,
                               roots=args.root)
    for name in sorted(resolved):
        for line in patches_mod.applies_to(resolved[name]):
            print(f"pnix: {name}: {line}", file=sys.stderr)
        if resolved[name].get("type") == "path":
            # A path pin carries no hash and names a directory on this machine
            # only. In a committed lock it breaks every other clone.
            print(
                f"pnix: {name}: is a path pin ({resolved[name]['path']}). It "
                f"has no hash and will not exist on another machine -- keep it "
                f"out of a committed declaration and use PNIX_OVERRIDE instead.",
                file=sys.stderr,
            )
    return 0


def cmd_init(args) -> int:
    for path in vendor.install(Path(args.project), force=args.force):
        print(f"wrote {path}")
    return 0


def cmd_look(args) -> int:
    project = Path(args.project)
    fresh, existing = _resolve_all(project, [], write=False, roots=args.root,
                                   prefetch=False)
    moved = False
    for name in sorted(fresh):
        old = existing.get(name, {}).get("rev")
        new = fresh[name].get("rev")
        if old and new and old != new:
            moved = True
            print(f"{name}: {old[:8]} -> {new[:8]}")
    for name in sorted(set(fresh) - set(existing)):
        moved = True
        print(f"{name}: not locked yet")
    for name in sorted(set(existing) - set(fresh)):
        moved = True
        print(f"{name}: locked but no longer declared")

    # Patches. `advice` reads the lock alone; `drift` costs one request per
    # tracked PR, which is cheap because `head` and `base` were stored at lock
    # time so nothing has to be diffed or cloned.
    for name in sorted(existing):
        node = existing[name]
        for line in (patches_mod.advice(node) + patches_mod.drift(node)
                     + patches_mod.applies_to(node)):
            moved = True
            print(f"{name}: {line}")
    if not moved:
        print("all pins current")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pnix")
    parser.add_argument("--project", default=".", help="project root")
    sub = parser.add_subparsers(dest="cmd", required=True)

    root_help = ("directory to scan for declarations; repeatable, "
                 "defaults to the project root")

    up = sub.add_parser("update", help="resolve and write the lock")
    up.add_argument("names", nargs="*", help="pins to update; default all")
    up.add_argument("--root", action="append", type=Path, default=None,
                    help=root_help)
    up.set_defaults(func=cmd_update)

    it = sub.add_parser("init", help="vendor the resolver into this project")
    it.add_argument("--force", action="store_true",
                    help="overwrite files that no longer carry the pnix marker")
    it.set_defaults(func=cmd_init)

    lk = sub.add_parser("look", help="report drift without writing")
    lk.add_argument("--root", action="append", type=Path, default=None,
                    help=root_help)
    lk.set_defaults(func=cmd_look)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
