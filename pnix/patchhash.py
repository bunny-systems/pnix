"""Compute a patched pin's tree hash at lock time.

The hash makes `applyPatches` a fixed-output derivation, which is what stops a
patched pin's store path depending on which nixpkgs applied the diff. Learning
it means building the tree, so this is the one place pnix builds anything.

The tree is built through pnix's *own* `resolver/eval/resolve.nix`, not the
project's vendored copy, which may predate this pnix -- `cli._warn_if_stale`
exists because it often does. Building it any other way would let the lock-time
expression and the resolver disagree about what the patched tree is, and a wrong
hash is the one error nothing downstream checks: a fixed-output derivation is
verified against its hash and never against its inputs.
"""

import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from . import lock, vendor

RESOLVER = Path(__file__).resolve().parent / "resolver" / "eval" / "resolve.nix"

# Same stance as prefetch.py: the resolver speaks plain Nix, and so does
# everything that drives it.
NO_EXPERIMENTAL = ["--option", "experimental-features", ""]

# Written beside the real lock rather than in a temp directory, because a `path`
# patch is recorded relative to the lock and resolved as `dirOf lockFile + path`
# (`resolve.nix`). A file under /tmp would make every local patch vanish.
TMP_NAME = "pins.lock.json.pnix-tmp"


class PatchHashError(Exception):
    """A patched tree could not be built, or could not be hashed."""


def _run(argv: list[str], who: str) -> subprocess.CompletedProcess:
    """`subprocess.run`, with a missing binary reported rather than raised.

    Everything here shells out to nix, and a `FileNotFoundError` escaping to the
    top is a traceback where the user needs one sentence naming the command they
    do not have.
    """
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise PatchHashError(
            f"{who}: {argv[0]} is not on PATH, and pnix needs it to hash a "
            f"patched tree. ({exc})"
        ) from exc


def needs_recompute(locked: dict, prior: dict, verify_all: bool = False) -> bool:
    """Whether this entry's `patchedHash` has to be built rather than carried.

    Keyed on the resolved patch nodes and the source rev, not on the hash being
    absent: an entry rewritten for an unrelated reason -- an edited `dir`, a
    repaired `lastModified` -- would otherwise pay a full rebuild to arrive at
    the identical hash, while an entry whose patch node moved must never keep
    the old one.

    `verify_all` is `pnix update --verify-patches`. Moving the nixpkgs pin is
    the one way the content can change with no patch node changing, because
    nixpkgs is what supplies `patch`; recomputing every patched pin on every
    nixpkgs bump would cost a full build to confirm a hash that almost never
    moves, so it is asked for rather than assumed.
    """
    if not locked.get("patches"):
        return False
    if verify_all:
        return True
    if "patchedHash" not in prior:
        return True
    if prior.get("patches") != locked.get("patches"):
        return True
    return prior.get("rev") != locked.get("rev")


def _nix_path(path: Path) -> str:
    """An absolute path as a Nix *path* expression, not a string.

    This distinction is load-bearing. A local patch is resolved as
    `dirOf lockFile + ("/" + patch.path)`: with a path value that yields a path,
    which Nix copies into the store when the derivation is built, so the sandbox
    can read it. With a string it yields a string, nothing is copied, and the
    build fails with "No such file or directory" on a path that plainly exists.

    Spelled `/. + "rel"` rather than as a bare literal, the idiom `fetchers.nix`
    already uses, because a bare path literal cannot contain a space.
    """
    return f'(/. + {json.dumps(str(path).lstrip("/"))})'


def _expression(lock_path: Path, names: list[str],
                nixpkgs_pin: str, select: str = "n: r.${n}") -> str:
    """The expression `nix-build` realises. Every eval-time knob is pinned here.

    `overrideVar = null` is not optional. Left to its default the resolver reads
    `PNIX_OVERRIDE` from the environment, so a developer with an override
    exported -- which is the workflow pnix itself recommends, and which normally
    lives in a shell for a whole session -- would have the *override's* tree
    hashed and that hash committed to the lock. Nothing later notices: the patch
    set has not moved, so `pnix update` keeps the poisoned value and every other
    machine fails with a fixed-output hash mismatch.

    `nixpkgsPin` likewise cannot be left to its default. It is an eval-time
    argument the consumer passes, so a project that renamed its nixpkgs pin has
    no way to reach this build -- the error would tell them to pass something
    they cannot pass here.
    """
    # An explicit list rather than `attrValues`, so the order of the built paths
    # is ours and not a property of how Nix happens to sort attribute names.
    wanted = " ".join(json.dumps(n) for n in names)
    return (
        f"let r = import {_nix_path(RESOLVER)} {{ "
        f"lockFile = {_nix_path(lock_path)}; "
        f"patchedOnly = true; "
        f"overrideVar = null; "
        f"nixpkgsPin = {json.dumps(nixpkgs_pin)}; "
        f"}}; in builtins.map ({select}) [ {wanted} ]"
    )


def _write_tmp_lock(project: Path, pins: dict[str, dict],
                    *, strip_hash: bool) -> Path:
    """The lock the resolver reads for this one call, beside the real one.

    `strip_hash` for a build: the hash being computed must not also be the hash
    being checked, or `patch.nix` builds a fixed-output derivation that
    validates against the stale value and fails instead of answering. Keep the
    hashes for a path lookup, where the fixed-output branch is exactly what
    makes the store path knowable without building anything.
    """
    # `vendor.DEST`, not a literal ".pnix": a `path` patch is resolved relative
    # to the lock, so if the vendored directory ever moves and this does not,
    # every local patch silently stops being found -- the exact failure TMP_NAME's
    # own comment exists to prevent.
    lock_dir = Path(project).resolve() / vendor.DEST
    lock_dir.mkdir(parents=True, exist_ok=True)
    tmp = lock_dir / TMP_NAME
    entries = {
        name: ({k: v for k, v in entry.items() if k != "patchedHash"}
               if strip_hash else entry)
        for name, entry in pins.items()
    }
    tmp.write_text(
        json.dumps({"schema": lock.SCHEMA, "pins": entries}, indent=2) + "\n"
    )
    return tmp


def compute(project: Path, pins: dict[str, dict], names: list[str],
            nixpkgs_pin: str = "nixpkgs") -> dict[str, str]:
    """Build the named pins' patched trees and hash them. name -> SRI hash."""
    if not names:
        return {}
    tmp = _write_tmp_lock(project, pins, strip_hash=True)
    try:
        proc = _run(
            ["nix-build", "--no-out-link",
             "-E", _expression(tmp, names, nixpkgs_pin), *NO_EXPERIMENTAL],
            ", ".join(names),
        )
        if proc.returncode != 0:
            raise PatchHashError(_build_failure(names, proc.stderr))
        paths = [line for line in proc.stdout.split("\n") if line.strip()]
        if len(paths) != len(names):
            raise PatchHashError(
                f"{', '.join(names)}: nix-build produced {len(paths)} paths for "
                f"{len(names)} pins; refusing to guess which is which."
            )
        built = dict(zip(names, paths, strict=True))
        # Deliberately *not* rooted here. The temp lock has the hashes stripped,
        # so this build took the input-addressed branch and these paths are not
        # the ones the resolver will name once the hash is recorded -- measured:
        # `ch7d91z…-demo-patched` here against `yd1qisr…-demo-patched` from the
        # fixed-output branch, same tree, same hash. Rooting happens in
        # `cli._hash_patches`, after the hashes are final, against `paths()`.
        hashes = {name: _hash_path(name, path) for name, path in built.items()}
        for name, path in built.items():
            _materialise(name, path, hashes[name])
        return hashes
    finally:
        tmp.unlink(missing_ok=True)


# How many lines of a failed build's log to show. A patch that does not apply
# says so within a few lines of the end; the rest is unpack noise, and the whole
# log for a nixpkgs-sized tree buries the one line that matters.
LOG_TAIL = 12


def _blamed(names: list[str], stderr: str) -> str | None:
    """Which pin's derivation the build actually failed on, if it says.

    One `nix-build` covers every recomputed pin, so its exit status alone cannot
    say which one broke -- and naming all of them reads, on a 21-pin config, as
    `pnix: a, b, c, d, e: could not build` while one patch is at fault.
    `applyPatches` names the derivation `<pin>-patched`, which is the thread to
    pull. Last match wins: the failing derivation is the last one mentioned.
    """
    found = None
    for line in stderr.splitlines():
        for name in names:
            if f"{name}-patched" in line:
                found = name
    return found


def _build_failure(names: list[str], stderr: str) -> str:
    tail = "\n".join(
        line for line in stderr.strip().splitlines()[-LOG_TAIL:]
        # pnix's own trace, which fires because the temp lock deliberately has no
        # hashes. Telling the reader to re-run the command they are running is
        # worse than saying nothing.
        if "has no patchedHash" not in line
    )
    blamed = _blamed(names, stderr)
    who = blamed or ", ".join(names)
    if "there is no pin called" in stderr:
        # The patch was never reached, so "fix the patch" is the wrong thread to
        # pull. `nixpkgsPin` is an eval-time argument the consumer passes and
        # cannot reach this build, which is what the flag exists for.
        advice = ("The nixpkgs that applies patches is not called `nixpkgs` "
                  "here; name it with `pnix update --nixpkgs-pin NAME`.")
    else:
        which = blamed or "<pin>"
        advice = (f"Fix the patch, or `pnix update --exclude {which}` to hold "
                  f"that pin and get the rest of the run.")
    return f"{who}: could not build the patched tree. {advice}\n{tail}"


def _hash_path(name: str, path: str) -> str:
    """The recursive (NAR) hash of a built tree, as SRI.

    `nix-hash`, not `nix hash path`: the latter is a `nix` subcommand and needs
    the nix-command experimental feature, which NO_EXPERIMENTAL switches off --
    so it fails on every real invocation while passing under any stub that
    answers by argv. Verified equal on a real tree: both spell it
    `sha256-XVVwVmNhl3JAxgVm4sm8IBGsvgRcmHTkxBWDE+t7CWc=`.

    NAR hashing is the default mode, which is the one the lock wants; `--flat`
    would be the wrong hash for a directory.
    """
    proc = _run(
        ["nix-hash", "--type", "sha256", "--sri", path, *NO_EXPERIMENTAL],
        name,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise PatchHashError(
            f"{name}: could not hash {path}. {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


# --- garbage-collection roots ----------------------------------------------
#
# A patched tree is a build *input*, so nothing in a system closure references
# it and `nix-store --delete` takes it without complaint -- verified. On a
# machine that collects garbage on a timer it is therefore gone by tomorrow, and
# the next evaluation pays the full `applyPatches` build again (46 s for a
# nixpkgs-sized tree). A root per patched pin is what makes "built once" true.
#
# The link lives outside the project, under $XDG_STATE_HOME, because a symlink
# into /nix/store inside the repo is something `git add -A` would pick up and
# `nix-store --add-root` would then keep alive after the project is deleted.
# tack does the same thing in the same place, which is where this shape is from.
ROOTS = "gcroots"


def _state_dir(project: Path) -> Path:
    """`$XDG_STATE_HOME/pnix/<key>/gcroots`, keyed by the project's path.

    Hashed rather than spelled out: two checkouts of one config must not share
    roots, project paths contain characters a directory name should not have to
    carry, and the key stays a fixed length however deep the checkout is.
    """
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    key = hashlib.sha256(
        str(Path(project).resolve()).encode()
    ).hexdigest()
    return Path(base) / "pnix" / key / ROOTS


def root(project: Path, built: dict[str, str]) -> None:
    """Keep each named store path alive, under one link per pin.

    Failure is reported and not raised. A missing root costs a rebuild; an
    update that aborted because it could not write a symlink would cost the
    user their whole run for a cache miss they did not ask about.
    """
    roots = _state_dir(project)
    try:
        roots.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"pnix: could not create {roots} ({exc}); patched trees will not "
              f"survive a garbage collection.", file=sys.stderr)
        return
    for name, path in sorted(built.items()):
        # `--realise` on a path that is not in the store would *build* it, and
        # an update must never start a 46 s build behind the user's back. A tree
        # that is already gone gets rooted the next time something builds it.
        if not Path(path).exists():
            continue
        link = roots / name
        proc = subprocess.run(
            # `--realise` is how `--add-root` is spelled for a path that already
            # exists; it registers the link and builds nothing. Not `--indirect`:
            # on nix 2.34.8 `--add-root` is already indirect, and `--query
            # --roots` confirms the registration either way.
            ["nix-store", "--realise", path, "--add-root", str(link),
             *NO_EXPERIMENTAL],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            print(f"pnix: {name}: could not root {path} "
                  f"({proc.stderr.strip()}); it will not survive a garbage "
                  f"collection.", file=sys.stderr)


def prune(project: Path, keep: set[str]) -> None:
    """Drop roots for pins that are no longer patched.

    Without this a pin that loses its patches keeps its last patched tree alive
    forever -- the one way this feature could leak store space rather than save
    rebuild time.
    """
    roots = _state_dir(project)
    if not roots.is_dir():
        return
    for link in sorted(roots.iterdir()):
        if link.name not in keep:
            link.unlink(missing_ok=True)


def paths(project: Path, pins: dict[str, dict], names: list[str],
          nixpkgs_pin: str = "nixpkgs") -> dict[str, str]:
    """Each named patched pin's store path, by evaluation alone.

    This exists for the pins `needs_recompute` held back. Their hash is already
    in the lock, so nothing is built and the path is never learned -- yet on a
    machine that only ever read the lock, that is precisely the tree with no
    root. Asking the resolver costs one evaluation (it imports the nixpkgs pin
    to build `patchPkgs`) and no build, because a recorded hash is what makes a
    fixed-output path knowable up front.
    """
    if not names:
        return {}
    tmp = _write_tmp_lock(project, pins, strip_hash=False)
    try:
        proc = subprocess.run(
            ["nix-instantiate", "--eval", "--strict", "--json",
             "-E", _expression(tmp, names, nixpkgs_pin,
                               select="n: r.${n}.outPath"),
             *NO_EXPERIMENTAL],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            # Not fatal, and deliberately quiet: the lock is already correct and
            # the only thing lost is a root. Shouting here would turn a cache
            # concern into noise on every update of a project whose nixpkgs pin
            # happens to be named something else.
            return {}
        try:
            found = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {}
        if not isinstance(found, list) or len(found) != len(names):
            return {}
        return dict(zip(names, found, strict=True))
    finally:
        tmp.unlink(missing_ok=True)


# --- putting the built tree where the lock says it is ----------------------
#
# Without this the tree is built *twice* per patch change: once input-addressed
# to learn the hash, and again as the fixed-output derivation that actually gets
# used, at a different store path with nothing connecting the two. Measured on a
# real project: `ch7d91z…-demo-patched` from the first build against
# `yd1qisr…-demo-patched` from the second, same bytes, same recorded hash.
#
# `nix-store --add-fixed --recursive sha256` lands on *exactly* the fixed-output
# path, verified against the resolver's own answer -- but only when the thing it
# reads is named `<pin>-patched`, because it takes the name from the basename and
# has no `--name`. A store path's basename carries a hash prefix, and the new
# CLI's `nix store add --name` needs an experimental feature this project
# switches off, so the tree is copied to a correctly-named directory first.
# Measured: a symlink does not work, `--add-fixed` hashes the link node itself.
#
# This name has to match `patch.nix`'s `name = "${name}-patched"`. A test greps
# for it, because a rename there would silently put us back to two builds.
SUFFIX = "-patched"


def _unlock(path: Path) -> None:
    """Make a copied store tree removable again.

    Store paths are read-only, `copytree` preserves that, and a mode-555
    directory cannot have its contents unlinked -- so the staging copy outlives
    the run unless the write bits go back on first.
    """
    for root, dirs, files in os.walk(path):
        for entry in (root, *(os.path.join(root, d) for d in dirs)):
            with contextlib.suppress(OSError):
                os.chmod(entry, os.stat(entry).st_mode | stat.S_IWUSR)
        for f in files:
            target = os.path.join(root, f)
            with contextlib.suppress(OSError):
                os.chmod(target, os.stat(target).st_mode | stat.S_IWUSR)


def _materialise(name: str, path: str, sri: str) -> str | None:
    """Add the built tree under the fixed-output path the lock now names.

    Returns that path, or None when it could not be done -- which is not an
    error: the lock is already correct, and the only consequence is the rebuild
    that used to happen unconditionally.

    Costs one copy of the tree (the staging directory honours `TMPDIR`, as nix's
    own builds do, so a nixpkgs-sized tree wants that much room there). That buys
    a whole `applyPatches` build, which for the same tree is 46 s plus a copy of
    its own inside the sandbox.
    """
    if not sri.startswith("sha256-"):
        return None
    staging = tempfile.mkdtemp(prefix="pnix-materialise-")
    target = Path(staging) / f"{name}{SUFFIX}"
    try:
        shutil.copytree(path, target, symlinks=True)
        proc = subprocess.run(
            ["nix-store", "--add-fixed", "--recursive", "sha256", str(target),
             *NO_EXPERIMENTAL],
            capture_output=True, text=True, check=False,
        )
        added = proc.stdout.strip()
        if proc.returncode != 0 or not added:
            print(f"pnix: {name}: could not place the patched tree at its "
                  f"locked path ({proc.stderr.strip()}); it will be rebuilt on "
                  f"first use.", file=sys.stderr)
            return None
        # A name that does not match means `patch.nix` and `SUFFIX` have drifted,
        # and the tree just added is a path nothing will ever ask for. Say so
        # rather than leaving a silent duplicate in the store.
        if not added.endswith(f"-{name}{SUFFIX}"):
            print(f"pnix: {name}: placed the patched tree at {added}, which is "
                  f"not the name the resolver asks for; it will be rebuilt.",
                  file=sys.stderr)
            return None
        return added
    except OSError as exc:
        print(f"pnix: {name}: could not stage the patched tree ({exc}); it will "
              f"be rebuilt on first use.", file=sys.stderr)
        return None
    finally:
        _unlock(Path(staging))
        shutil.rmtree(staging, ignore_errors=True)
