"""pnix command line.

update  collect declarations, resolve refs to revs, hash, write the lock
look    resolve refs without writing or downloading, report what moved
init    copy the eval-time resolver into this repo
"""

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pnix import (
    __version__,
    discover,
    patchhash,
    refcache,
    refs,
    schema,
    sources,
    vendor,
)
from pnix import collect as collect_mod
from pnix import lock as lock_mod
from pnix import patches as patches_mod
from pnix import spinner as spinner_mod

# The lock lives beside the vendored resolver: one directory is the whole
# of pnix in a consumer repo, so `rm -rf .pnix` uninstalls it.
LOCK_NAME = vendor.DEST / "pins.lock.json"
ATTR = "pins"


# Declaration fields that do not affect what gets fetched.
NON_FETCH_FIELDS = {"patches", "importable", "follows", "excludeFollow", "flake",
                    "dir", "forge"}

# Declaration fields that are pnix bookkeeping but must reach the resolver.
CARRIED_FIELDS = ("importable", "follows", "excludeFollow", "flake", "dir")


class ProjectError(Exception):
    pass


class UsageError(Exception):
    """A command that cannot do what it was asked. Never a silent no-op."""


def find_project(start: Path | None) -> Path:
    """The nearest directory at or above `start` that holds a `.pnix/`.

    Anchoring on the vendored directory is what lets `pnix look` work from
    anywhere inside a config repo, the way `git status` does. Without it
    `--project` defaults to the working directory, so running from
    `~/nixconfig/modules` silently treats `modules/` as the project: no lock,
    every pin reported as "not locked yet".

    An explicit `--project` is taken literally and never searched upward -- if
    you named a directory, you meant that directory.
    """
    if start is not None:
        return Path(start)

    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / vendor.DEST).is_dir():
            return candidate
    raise ProjectError(
        f"no {vendor.DEST}/ in {here} or any parent. Run `pnix init` in the "
        f"project root first, or name it with --project."
    )


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


class Progress:
    """Per-pin progress, on stderr, as each pin finishes.

    `pnix update` on a real config is ~20 s of network with nothing on screen,
    which is indistinguishable from a hang. Printed as pins *complete* rather
    than as they start, because the pool runs them concurrently and a list of
    "starting..." lines in arbitrary order says less than a result does.

    stderr, so `pnix update > somewhere` still captures only what the command
    is for.
    """

    def __init__(self, total: int, quiet: bool = False, verbose: bool = False,
                 animate: bool = True):
        self.total = total
        self.quiet = quiet or total == 0
        self.verbose = verbose and not self.quiet
        self.done = 0
        self.counts = dict.fromkeys(Progress.STATES, 0)
        self.width = 0
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._inflight: set[str] = set()
        # Inert off a tty, so nothing below has to ask whether to animate.
        #
        # Deliberately not tied to `quiet`. `look` passes quiet=True because it
        # prints its own report rather than per-pin lines, and it is the command
        # most in need of a spinner: a cold run is ~35 s of nothing. `-q` is the
        # only thing that silences the animation, because that is a request for
        # silence rather than for a different report.
        self.spinner = spinner_mod.Spinner(sys.stderr, total=total)
        if not animate or total == 0:
            self.spinner.enabled = False
        if not self.quiet:
            self._say(f"pnix: resolving {total} pin{'s' * (total != 1)}")

    def _say(self, text: str) -> None:
        """Every line goes through the spinner, which owns the last row.

        Off a tty this is a plain write; on one it clears the animation, prints,
        and leaves the next tick to redraw underneath.
        """
        self.spinner.write_line(text)

    def __enter__(self):
        self.spinner.start()
        return self

    def __exit__(self, *exc):
        self.spinner.stop()
        return False

    def tick(self) -> None:
        with self._lock:
            self.spinner.update(self._inflight, done=self.done)
        self.spinner.tick()

    def began(self, name: str) -> None:
        with self._lock:
            self._inflight.add(name)

    def fetching(self, name: str) -> None:
        """Announced *before* the download, because that is where the time is.

        Behind `-v`. With 24 pins the `[n/total]` counter already shows the run
        moving, and interleaving two lines per pin from eight threads buries the
        results -- which are what the command is for. It earns its place on a
        run of one or two slow pins, where nothing else moves for 15 s.
        """
        if self.verbose:
            self._say(f"  fetching {name}...")

    #: A pin has no upstream to be "ahead" or "diverged" *of* -- the lock holds
    #: one rev, and saying more would mean a commit-graph walk per pin.
    STATES = ("new", "updated", "repaired", "relocked", "unchanged")

    def finish(self, name: str, before: dict, after: dict,
               repaired: bool = False) -> None:
        old, new = before.get("rev"), after.get("rev")
        if not before:
            state, detail = "new", (new or "?")[:8]
        elif old == new:
            # Three different things share "the rev did not move", and calling
            # them all `unchanged` is how a frozen entry stayed invisible.
            # `repaired` is specifically a node missing something `prefetch`
            # should have produced; a declaration edit rewrites the entry too,
            # and naming that a repair would say something was broken when
            # nothing was.
            short = (old or "?")[:8]
            if repaired:
                state, detail = "repaired", f"{short}  (refilled)"
            elif before == after:
                state, detail = "unchanged", short
            else:
                state, detail = "relocked", f"{short}  (declaration changed)"
        else:
            state, detail = "updated", f"{(old or '?')[:8]} -> {(new or '?')[:8]}"

        with self._lock:
            self.done += 1
            self.counts[state] += 1
            self._inflight.discard(name)
            line = (f"  [{self.done:>{len(str(self.total))}}/{self.total}] "
                    f"{name:<{self.width}}  {state:<9} {detail}")
        if not self.quiet:
            self._say(line)

    def summary(self) -> None:
        self.spinner.stop()
        if self.quiet:
            return
        secs = time.monotonic() - self.started
        parts = [f"{self.counts[s]} {s}" for s in self.STATES if self.counts[s]]
        self._say(
            f"pnix: {self.done} pin{'s' * (self.done != 1)} resolved"
            f"{' -- ' + ', '.join(parts) if parts else ''}, {secs:.1f}s"
        )


def _hash_patches(project: Path, result: dict, existing: dict,
                  verify_patches: bool, nixpkgs_pin: str = "nixpkgs") -> None:
    """Fill in `patchedHash` for every patched pin that needs one.

    A patched pin's store path comes from this hash, so a pin whose patch set
    and rev are unchanged keeps the recorded one rather than paying a rebuild to
    arrive at the same value. `needs_recompute` owns that decision.
    """
    todo = [
        name for name in sorted(result)
        if patchhash.needs_recompute(result[name], existing.get(name, {}),
                                     verify_all=verify_patches)
    ]
    carried = []
    for name, entry in result.items():
        prior = existing.get(name, {})
        if (entry.get("patches") and name not in todo
                and "patchedHash" in prior):
            entry["patchedHash"] = prior["patchedHash"]
            carried.append(name)
    if not todo:
        _root_patched(project, result, nixpkgs_pin)
        return
    fresh = patchhash.compute(project, result, todo,
                              nixpkgs_pin=nixpkgs_pin)
    for name, value in fresh.items():
        prior = existing.get(name, {})
        was = prior.get("patchedHash")
        # Only worth saying when the patch set and the rev were both held still,
        # because then the nixpkgs applying the diff is the only thing left to
        # explain it. After `--repatch` adopted new commits, a different hash is
        # the expected outcome and this sentence would be false.
        held_still = (prior.get("patches") == result[name].get("patches")
                      and prior.get("rev") == result[name].get("rev"))
        if was is not None and was != value and held_still:
            print(
                f"pnix: {name}: patched tree changed ({was} -> {value}). The "
                f"patch set is the same, so the nixpkgs applying it produced "
                f"different bytes.",
                file=sys.stderr,
            )
        result[name]["patchedHash"] = value
    _root_patched(project, result, nixpkgs_pin)


def _root_patched(project: Path, result: dict, nixpkgs_pin: str) -> None:
    """Keep every patched pin's tree out of the garbage collector.

    A patched tree is a build *input*, so nothing in a system closure holds it
    and a timed collection takes it -- then the next evaluation pays the whole
    `applyPatches` build again. One link per patched pin fixes that.

    Called after the hashes are final and resolved through `paths()`, not from
    the hashes themselves: the path the resolver names comes from the recorded
    hash, and `compute` builds with the hash *stripped*, so its own output is a
    different store path. Rooting that one would protect a tree nothing asks
    for.

    A pin whose fixed-output tree has not been built yet is skipped rather than
    realised, so it stays unrooted until something builds it and the next
    update picks it up -- the same re-rooting `tack` does on every run.
    """
    patched = sorted(name for name, entry in result.items()
                     if entry.get("patches"))
    patchhash.prune(project, set(patched))
    if patched:
        patchhash.root(
            project,
            patchhash.paths(project, result, patched, nixpkgs_pin=nixpkgs_pin),
        )


def _resolve_all(project: Path, names: list[str], write: bool,
                 roots: list[Path] | None = None,
                 prefetch: bool = True,
                 quiet: bool = True,
                 verbose: bool = False,
                 exclude: list[str] | None = None,
                 workers: int | None = None,
                 cache: refcache.Cache | None = None,
                 animate: bool = True,
                 verify_patches: bool = False,
                 repatch: list[str] | None = None,
                 nixpkgs_pin: str = "nixpkgs") -> tuple[dict, dict]:
    """Resolve every declared pin. Returns (resolved, previous lock contents).

    `prefetch=False` is what makes `pnix look` cheap: resolving a ref is one
    `git ls-remote`, while hashing means downloading the source. Drift can be
    reported from the rev alone.
    """
    lock_path = project / LOCK_NAME
    existing, migrated_from = lock_mod.read_at(lock_path)
    if migrated_from is not None:
        # The lock is carried forward rather than discarded, so no pin loses its
        # rev. The *resolver* in this repo is the stale half: it checks the
        # schema and will refuse the file this run is about to write.
        print(
            f"pnix: lock is schema {migrated_from}, migrating to "
            f"{lock_mod.SCHEMA}. Run `pnix update` to write it and `pnix init` "
            f"to re-vendor the resolver: the resolver compares the schema for "
            f"equality, so a repo with only one of the two updated does not "
            f"evaluate. Commit the lock and .pnix/ in one commit.",
            file=sys.stderr,
        )
    pins, _ = _collect(project, roots)

    exclude = set(exclude or ())

    # A name that matches nothing resolved nothing and kept every existing
    # entry, so a typo looked exactly like a successful no-op. The same applies
    # to `--exclude`, and more sharply: a misspelled exclusion updates the very
    # pin it was meant to hold still.
    unknown = [
        n for n in [*(names or ()), *sorted(exclude), *(repatch or ())]
        if n not in pins
    ]
    if unknown:
        known = ", ".join(sorted(pins)) or "none declared"
        raise UsageError(
            f"{', '.join(unknown)}: not a declared pin. known: {known}"
        )

    # `--repatch foo` where foo declares no patches resolves nothing to re-fetch,
    # so it reads as a successful no-op while the pin the user meant goes
    # untouched -- the same trap as a misspelled name, one level down.
    unpatched = sorted(n for n in (repatch or ()) if not pins[n].get("patches"))
    if unpatched:
        raise UsageError(
            f"{', '.join(unpatched)}: --repatch names a pin that declares no "
            f"patches, so there is nothing to re-resolve. Drop it, or run "
            f"`pnix update {' '.join(unpatched)}` to move the pin itself."
        )

    # Holding a pin still means keeping the entry it already has. One that was
    # never locked has no entry to keep, so excluding it would drop it from the
    # lock -- the opposite of what the flag is for.
    never_locked = sorted(n for n in exclude if n not in existing)
    if never_locked:
        raise UsageError(
            f"{', '.join(never_locked)}: excluded but never locked, so there is "
            f"nothing to hold. Drop the --exclude, or run once without it."
        )

    # Pruning is the destructive half of `update`, and it used to be the silent
    # one: scoping a run with `--root` to a file that no longer holds every
    # declaration drops the rest from the lock, with no output at all when the
    # scoped set is empty.
    if not names:
        for gone in sorted(set(existing) - set(pins)):
            print(f"pnix: {gone}: locked but no longer declared -- removing",
                  file=sys.stderr)

    result: dict[str, dict] = {}
    todo: dict[str, dict] = {}
    for name, spec in pins.items():
        if names and name not in names:
            if name in existing:
                result[name] = existing[name]
            continue
        if name in exclude:
            result[name] = existing[name]
            rev = (existing[name].get("rev") or "?")[:8]
            print(f"pnix: {name}: excluded, keeping {rev}", file=sys.stderr)
            continue
        todo[name] = spec

    # Each pin is an independent network round-trip: ~0.86 s for the ls-remote
    # alone, so 21 pins serially is ~18 s. Pool the whole per-pin pipeline
    # (resolve, then prefetch when the rev actually moved).
    progress = Progress(len(todo), quiet=quiet, verbose=verbose,
                        animate=animate)
    # Names are known up front, so the result column can line up.
    progress.width = max((len(n) for n in todo), default=0)

    def one(name: str) -> tuple[str, dict]:
        progress.began(name)
        spec = todo[name]
        # `type` is optional when the URL's host says what runs there;
        # schema.validate has already refused anything it could not settle.
        src = sources.get(schema.type_of(spec))
        locked = src.resolve(spec)

        prior = existing.get(name, {})
        # Missing something `prefetch` should have produced: stale however well
        # the fetch fields match, or the entry is frozen incomplete forever.
        incomplete = bool(prior) and not all(
            k in prior for k in getattr(src, "prefetch_keys", ())
        )
        # `_unchanged` compares the *fetch* fields, which are precisely the ones
        # CARRIED_FIELDS are not. Without this, an edit adding only
        # `excludeFollow` or `dir` is reported unchanged and never written, so
        # the declaration silently has no effect -- and for `dir` that means a
        # flake kept being looked for in the wrong directory.
        carried_stale = any(prior.get(k) != spec.get(k) for k in CARRIED_FIELDS)
        # `--repatch` is the only way a moved PR head enters the lock. A patch
        # declaration does not change when the branch it tracks does --
        # `_patches_changed` compares declarations -- and adopting someone's
        # force-pushed branch during an `update` run for something else is not a
        # default worth having, so it is asked for. `repatch = []` means all.
        repatching = repatch is not None and (
            name in repatch if repatch else bool(spec.get("patches"))
        )
        # A local patch file lives in the consumer's tree, so the lock cannot
        # pin it the way it pins a download -- only its recorded hash can say it
        # moved. Without this an edited patch kept its `patchedHash`, and on the
        # machine that wrote it the patched tree already existed, so Nix handed
        # back the pre-edit tree with no rebuild and no error.
        local_moved = patches_mod.local_patch_drifted(prior, project)
        if (_unchanged(spec, prior)
                and not incomplete
                and not carried_stale
                and not repatching
                and not local_moved
                and prior.get("rev") == locked.get("rev")
                and not _patches_changed(spec, prior)):
            progress.finish(name, prior, prior)
            return name, prior

        if prefetch:
            progress.fetching(name)
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

        # A repair that produced nothing is not a repair. Saying "repaired"
        # while writing back an identical entry is how a pin stayed broken
        # across several runs, each of them reporting success.
        still_missing = [
            k for k in getattr(src, "prefetch_keys", ()) if k not in locked
        ]
        if prefetch and still_missing:
            print(
                f"pnix: {name}: no {', '.join(still_missing)} could be "
                f"determined for this source. A pin without `lastModified` "
                f"builds as `...19700101.<rev>`; please report the url and "
                f"`nix --version`.",
                file=sys.stderr,
            )
        progress.finish(
            name, prior, locked, repaired=incomplete and not still_missing
        )
        return name, locked

    if todo:
        # The cache is installed for the duration of the pool and flushed once
        # after it: eight threads share the object, so reading before they start
        # and writing after they finish avoids locking the file per access.
        refs.CACHE = cache
        stop_ticking = threading.Event()

        def animate() -> None:
            # A daemon would do, but an explicit stop means the last frame is
            # always erased -- a killed thread can leave the line drawn.
            while not stop_ticking.wait(0.1):
                progress.tick()

        ticker = threading.Thread(target=animate, daemon=True)
        try:
            with progress:
                ticker.start()
                # One pin is one network round-trip, so the pool is sized by how
                # many requests are worth having in flight, not by cores. Capped
                # at the number of pins because an idle thread still costs one.
                with ThreadPoolExecutor(
                    max_workers=min(workers or refs.DEFAULT_WORKERS, len(todo))
                ) as pool:
                    for name, locked in pool.map(one, list(todo)):
                        result[name] = locked
        finally:
            stop_ticking.set()
            ticker.join(timeout=1)
            refs.CACHE = None
            if cache is not None:
                cache.save()
    progress.summary()

    # `prefetch=False` is `look`, which reports through cmd_look instead and
    # must never build.
    if prefetch:
        # Said before the hashing below, not after: hashing builds, a build can
        # fail, and a merged PR is one of the likeliest reasons a patch stopped
        # applying. Reporting it only on success would withhold the explanation
        # exactly when it is needed.
        #
        # `advice` reads the entry `update` just wrote and costs no request, so
        # there is no reason to make it exclusive to `look`. `drift` stays out --
        # it compares the locked head against the current one, which `update`
        # has just made identical.
        for name in sorted(result):
            for line in (patches_mod.advice(result[name])
                         + patches_mod.applies_to(result[name])):
                print(f"pnix: {name}: {line}", file=sys.stderr)
            if result[name].get("type") == "path":
                # A path pin carries no hash and names a directory on this
                # machine only. In a committed lock it breaks every other clone.
                print(
                    f"pnix: {name}: is a path pin ({result[name]['path']}). It "
                    f"has no hash and will not exist on another machine -- keep "
                    f"it out of a committed declaration and use PNIX_OVERRIDE "
                    f"instead.",
                    file=sys.stderr,
                )

        # Patched trees are hashed after the pool, not inside it: the pool is
        # sized for network round-trips, while this is one `nix-build` that Nix
        # parallelises itself.
        _hash_patches(project, result, existing, verify_patches,
                      nixpkgs_pin)

    if write:
        lock_mod.write(lock_path, result)
    return result, existing


def _cache_for(args, names: list[str] | None = None) -> refcache.Cache | None:
    """The cache this run may use, or None.

    Two ways to say "ask the remote": `--refresh`, and naming a pin. Naming one
    is already how you say "that pin, now", so honouring the cache for it would
    contradict the request -- and a named run resolves only what was named, so
    there is nothing else to keep cheap.
    """
    if args.refresh or names:
        return None
    return refcache.open_default()


def _warn_if_stale(project: Path) -> None:
    """Tell a consumer their vendored resolver predates this pnix.

    Only when something is vendored: an empty `.pnix/` is a project nobody has
    run `init` in, and Nix says so loudly the moment anything imports it.
    """
    if not any((project / vendor.DEST).rglob(f"*{vendor.MARKABLE}")):
        return
    drift = vendor.stale(project)
    if not drift:
        return
    shown = ", ".join(str(p.relative_to(project)) for p in drift[:4])
    more = f", +{len(drift) - 4} more" if len(drift) > 4 else ""
    print(
        f"pnix: {vendor.DEST}/ is not what this pnix writes "
        f"({len(drift)} file{'' if len(drift) == 1 else 's'}: {shown}{more}). "
        f"Re-run `pnix init`: the lock and the code that reads it ship together, "
        f"so a fix to the resolver only reaches this project when you vendor it.",
        file=sys.stderr,
    )


def cmd_update(args) -> int:
    project = find_project(args.project)
    _warn_if_stale(project)
    _resolve_all(project, args.names, write=True,
                               roots=args.root, quiet=args.quiet,
                               verbose=args.verbose, exclude=args.exclude,
                               verify_patches=args.verify_patches,
                               repatch=args.repatch,
                               nixpkgs_pin=args.nixpkgs_pin,
                               workers=args.workers,
                               cache=_cache_for(args, args.names),
                               animate=not args.quiet)
    return 0


def cmd_init(args) -> int:
    # `init` is the one command that must work where no `.pnix/` exists yet, so
    # it takes the working directory rather than searching for one.
    project = Path(args.project or ".")
    # Asked before writing, because afterwards nothing differs. `init` is now
    # routine -- it is how a resolver fix reaches a repo -- so nine identical
    # `wrote` lines every time says nothing about what actually moved.
    changing = set(vendor.stale(project))
    written = vendor.install(project, force=args.force)
    for path in written:
        if path in changing:
            print(f"wrote {path}")
    if not changing:
        print(f"{vendor.DEST}/ is up to date ({len(written)} files)")
    return 0


def cmd_look(args) -> int:
    project = find_project(args.project)
    _warn_if_stale(project)
    cache = _cache_for(args)
    fresh, existing = _resolve_all(project, [], write=False, roots=args.root,
                                   prefetch=False, workers=args.workers,
                                   cache=cache)
    moved = False
    # Same width-aligned column `update` prints, so the two commands read as one
    # tool. Widest of everything that might be named, since the three loops below
    # draw from different sets.
    width = max((len(n) for n in set(fresh) | set(existing)), default=0)

    def row(name: str, detail: str) -> None:
        nonlocal moved
        moved = True
        print(f"{name:<{width}}  {detail}")

    for name in sorted(fresh):
        was = existing.get(name, {}).get("rev")
        now = fresh[name].get("rev")
        if was and now and was != now:
            row(name, f"{was[:8]} -> {now[:8]}")
    for name in sorted(set(fresh) - set(existing)):
        row(name, "not locked yet")
    for name in sorted(set(existing) - set(fresh)):
        row(name, "locked but no longer declared")

    # Patches. `advice` reads the lock alone; `drift` costs one request per
    # tracked PR, which is cheap because `head` and `base` were stored at lock
    # time so nothing has to be diffed or cloned.
    for name in sorted(existing):
        node = existing[name]
        for line in (patches_mod.advice(node) + patches_mod.drift(node, name)
                     + patches_mod.applies_to(node)):
            row(name, line)
    if not moved:
        print("all pins current")

    # `look` reports drift, so a cached answer is precisely when it would fail
    # to. Stale is acceptable; silently stale is not.
    if cache is not None and cache.ages:
        oldest = max(cache.ages.values())
        print(f"pnix: {len(cache.ages)} ref"
              f"{'' if len(cache.ages) == 1 else 's'} answered from cache, "
              f"up to {int(oldest // 60)}m{int(oldest % 60)}s old "
              f"-- pass --refresh to re-check",
              file=sys.stderr)
    # Drift is not an error -- `look` is a report, and a report that fails is
    # useless in a pipeline. `--exit-code` is for the caller that wants a gate.
    return 1 if (moved and args.exit_code) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pnix")
    # Worth having because a pnix older than the lock refuses it outright, so
    # "which pnix is this" is the first question when that happens.
    parser.add_argument("--version", action="version",
                        version=f"pnix {__version__}")
    parser.add_argument(
        "--project", default=None,
        help="project root; default: the nearest directory at or above the "
             "working directory containing .pnix/ (for init: the working "
             "directory)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    root_help = ("directory to scan for declarations; repeatable, "
                 "defaults to the project root")
    workers_help = (f"how many pins to resolve at once "
                    f"(default {refs.DEFAULT_WORKERS})")
    refresh_help = (f"ignore cached ref lookups (they expire after "
                    f"{refcache.TTL // 60} minutes)")

    up = sub.add_parser("update", help="resolve and write the lock")
    up.add_argument("names", nargs="*", help="pins to update; default all")
    up.add_argument("--root", action="append", type=Path, default=None,
                    help=root_help)
    up.add_argument("-q", "--quiet", action="store_true",
                    help="no per-pin progress; warnings and errors still print")
    up.add_argument("-v", "--verbose", action="store_true",
                    help="also report each download as it starts")
    up.add_argument("--exclude", action="append", metavar="NAME", default=None,
                    help="hold this pin at its locked revision; repeatable")
    up.add_argument("--workers", type=int, default=None, metavar="N",
                    help=workers_help)
    up.add_argument("--refresh", action="store_true", help=refresh_help)
    up.add_argument(
        "--nixpkgs-pin", metavar="NAME", default="nixpkgs",
        help="which pin supplies the nixpkgs that applies patches, when it is "
             "not called `nixpkgs`; matches the resolver's `nixpkgsPin`",
    )
    up.add_argument(
        "--repatch", nargs="*", metavar="NAME", default=None,
        help="re-resolve patch nodes for these pins (all patched pins when "
             "none are named), adopting a tracked PR's new commits",
    )
    up.add_argument(
        "--verify-patches", action="store_true",
        help="rebuild every patched pin's tree and re-check its hash, even "
             "when only the nixpkgs applying the patch moved",
    )
    up.set_defaults(func=cmd_update)

    it = sub.add_parser("init", help="vendor the resolver into this project")
    it.add_argument("--force", action="store_true",
                    help="overwrite files that no longer carry the pnix marker")
    it.set_defaults(func=cmd_init)

    lk = sub.add_parser("look", help="report drift without writing")
    lk.add_argument("--root", action="append", type=Path, default=None,
                    help=root_help)
    lk.add_argument("--workers", type=int, default=None, metavar="N",
                    help=workers_help)
    lk.add_argument("--refresh", action="store_true", help=refresh_help)
    lk.add_argument("--exit-code", action="store_true",
                    help="exit 1 when anything has drifted, for CI")
    lk.set_defaults(func=cmd_look)

    args = parser.parse_args(argv)
    try:
        if getattr(args, "workers", None) is not None and args.workers < 1:
            raise UsageError(
                f"--workers must be at least 1, got {args.workers}. "
                f"ThreadPoolExecutor refuses a pool of none."
            )
        return args.func(args)
    except (ProjectError, UsageError, patchhash.PatchHashError) as e:
        # Running outside a project is a usage mistake, not a crash.
        print(f"pnix: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
