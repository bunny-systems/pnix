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

import json
import subprocess
from pathlib import Path

from . import lock

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
                nixpkgs_pin: str) -> str:
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
        f"}}; in builtins.map (n: r.${{n}}) [ {wanted} ]"
    )


def compute(project: Path, pins: dict[str, dict], names: list[str],
            nixpkgs_pin: str = "nixpkgs") -> dict[str, str]:
    """Build the named pins' patched trees and hash them. name -> SRI hash."""
    if not names:
        return {}
    lock_dir = Path(project) / ".pnix"
    lock_dir.mkdir(parents=True, exist_ok=True)
    tmp = lock_dir / TMP_NAME

    # The hash being computed must not also be the hash being checked. Left in
    # place, `patch.nix` builds a fixed-output derivation that validates against
    # the stale value and fails instead of answering.
    stripped = {
        name: {k: v for k, v in entry.items() if k != "patchedHash"}
        for name, entry in pins.items()
    }
    tmp.write_text(
        json.dumps({"schema": lock.SCHEMA, "pins": stripped}, indent=2) + "\n"
    )
    try:
        proc = subprocess.run(
            ["nix-build", "--no-out-link",
             "-E", _expression(tmp, names, nixpkgs_pin), *NO_EXPERIMENTAL],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            raise PatchHashError(_build_failure(names, proc.stderr))
        paths = [line for line in proc.stdout.split("\n") if line.strip()]
        if len(paths) != len(names):
            raise PatchHashError(
                f"{', '.join(names)}: nix-build produced {len(paths)} paths for "
                f"{len(names)} pins; refusing to guess which is which."
            )
        return {
            name: _hash_path(name, path)
            for name, path in zip(names, paths, strict=True)
        }
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
    proc = subprocess.run(
        ["nix-hash", "--type", "sha256", "--sri", path, *NO_EXPERIMENTAL],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise PatchHashError(
            f"{name}: could not hash {path}. {proc.stderr.strip()}"
        )
    return proc.stdout.strip()
