"""Fetch a source and return its hash.

Uses only the stable Nix CLI: `nix-prefetch-url` and `nix-hash`, both of which
work with `--option experimental-features ""`.

Not `nix hash to-sri`, which the design named: every `nix <subcommand>` is
gated behind the `nix-command` experimental feature, so calling it with
NO_EXPERIMENTAL disables the thing being invoked --

    error: experimental Nix feature 'nix-command' is disabled

`nix-hash --to-sri` is the same conversion on the stable CLI. Measured on
Nix 2.34.8.
"""

import datetime
import io
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

# Every nix invocation carries this so pnix never depends on the user's
# experimental-features setting, in either direction.
NO_EXPERIMENTAL = ["--option", "experimental-features", ""]


class PrefetchError(Exception):
    pass


def to_sri(base32: str) -> str:
    """Convert nix-prefetch-url's base32 digest to the SRI the lock stores."""
    proc = subprocess.run(
        ["nix-hash", "--type", "sha256", "--to-sri", base32, *NO_EXPERIMENTAL],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise PrefetchError(f"nix-hash --to-sri {base32}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def resolve_redirect(url: str) -> str:
    """Follow redirects and return the URL actually served.

    A pin that names a moving target -- a channel, or a `latest.tar.gz` -- must
    be locked to the concrete artefact behind it, or the lock records a pointer
    and the hash beside it goes stale the moment the pointer moves. Measured:
    `channels.nixos.org/nixos-unstable/nixexprs.tar.xz` resolves to
    `releases.nixos.org/nixos/unstable/nixos-26.11pre1070770.8ce4ef6cb6f8/nixexprs.tar.xz`,
    which names the exact nixpkgs revision in its path.

    HEAD rather than GET: nothing is downloaded here, and the caller hashes the
    resolved URL afterwards.
    """
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "pnix"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.url
    except OSError as err:
        raise PrefetchError(f"resolving {url}: {err}") from err


def _hash_local(path: Path, url: str) -> str:
    proc = subprocess.run(
        ["nix-prefetch-url", "--unpack", "--type", "sha256", f"file://{path}",
         *NO_EXPERIMENTAL],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise PrefetchError(f"nix-prefetch-url {url}: {proc.stderr.strip()}")
    out = proc.stdout.strip().splitlines()
    if not out:
        raise PrefetchError(f"nix-prefetch-url {url}: no hash on stdout")
    return to_sri(out[-1])


def _archive_mtime(blob: bytes) -> int | None:
    """The commit timestamp, read out of the archive itself.

    A forge archive stamps every entry with the commit date, so the tarball
    carries the one fact `git ls-remote` cannot give us. Verified against
    consumer #1: the first entry's mtime equals, to the second, the
    `lastModified` tack obtains from `fetchTree`.

    This matters more than it sounds. nixpkgs' own flake builds its version
    string from `self.lastModifiedDate` and `self.shortRev`; without them every
    NixOS system derivation is named `...19700101.dirty` and its store path
    differs from an otherwise identical build. The alternative source for this
    is a forge API call, which needs a token and is rate-limited -- exactly what
    pnix exists to avoid.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tf:
            first = tf.next()
            return int(first.mtime) if first is not None else None
    except (tarfile.TarError, OSError):
        return None


def date_of(epoch: int) -> str:
    """`lastModified` -> the YYYYMMDDHHMMSS string flakes call lastModifiedDate.

    Nix formats this in C++; there is no strftime in builtins, so it has to be
    computed here and stored, or the vendored resolver cannot produce it.
    """
    stamp = datetime.datetime.fromtimestamp(epoch, datetime.UTC)
    return stamp.strftime("%Y%m%d%H%M%S")


def file(url: str) -> str:
    """Download `url` unchanged; return the SRI hash of the file itself.

    A **flat** hash, not a NAR hash -- this is what `builtins.fetchurl { url;
    sha256 = ...; }` expects, and it is why `file` is a separate fetch primitive
    from `tarball` rather than a flag on it. Feeding a NAR hash here fails at
    fetch time with a hash mismatch that says nothing about the cause.
    """
    proc = subprocess.run(
        ["nix-prefetch-url", "--type", "sha256", url, *NO_EXPERIMENTAL],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise PrefetchError(f"nix-prefetch-url {url}: {proc.stderr.strip()}")
    out = proc.stdout.strip().splitlines()
    if not out:
        raise PrefetchError(f"nix-prefetch-url {url}: no hash on stdout")
    return to_sri(out[-1])


def tarball(url: str) -> tuple[str, int | None]:
    """Download and unpack `url`; return (SRI hash of the NAR, lastModified).

    The hash is what `builtins.fetchTarball { sha256 = ...; }` expects.

    Downloaded here rather than by `nix-prefetch-url` so the bytes can be read
    twice -- once for the hash, once for the archive mtime -- without fetching
    twice. Hashing a local copy yields the same NAR hash as hashing the remote
    URL, since the hash is of the unpacked tree.
    """
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            blob = resp.read()
    except OSError as err:
        raise PrefetchError(f"fetching {url}: {err}") from err

    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "source.tar.gz"
        local.write_bytes(blob)
        sri = _hash_local(local, url)
    return sri, _archive_mtime(blob)
