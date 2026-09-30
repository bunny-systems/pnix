"""Cache `git ls-remote` answers between commands.

`pnix look` followed by `pnix update` resolves every ref twice, and on a large
repository that is the whole cost of the command: measured on a 20-pin config,
nixpkgs alone took 4-23 s of an 11 s run while every other pin answered in
0.5-0.7 s. Collecting the declarations, for comparison, took 0.04 s.

**Nothing here validates an entry, because nothing can.** A conditional HTTP
request with an ETag still costs a GitHub rate-limit unit -- measured, 304 and
`x-ratelimit-remaining` down by one -- and `git ls-remote` has no conditional
form at all. Resolving is the check. So an entry is good because it is recent
and because the caller did not ask for fresh, and for no other reason.

Which way staleness errs decides who may use it. For `update` a cached rev
means locking something up to a TTL old: a real rev, just not the newest, and
the next run moves it. For `look` it means reporting "all pins current" when a
pin has moved, which is the one thing that command exists to catch -- so `look`
reports the age of what it used rather than hiding it. See `Cache.ages`.
"""

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

#: Bump when the on-disk shape changes. A file this pnix cannot read is
#: discarded rather than guessed at -- the same rule as the lock's `schema`.
VERSION = 1

#: One hour, matching Nix's own `tarball-ttl` default, so the semantics are
#: borrowed rather than invented and `--refresh` means what it does elsewhere.
TTL = 3600


def default_path() -> Path | None:
    """`$XDG_CACHE_HOME/pnix/refs.json`, or `~/.cache` when unset.

    Not inside `.pnix/`: the cache is machine state shared by every project on
    it, and a committed directory is the wrong place for something that would
    show up in a diff. Sharing it between projects is deliberate -- two repos
    pinning the same url and ref within one TTL then agree on the rev instead of
    drifting apart by however long passed between the two commands.

    None when there is nowhere to put it. `Path.home()` raises with no HOME and
    no passwd entry, which is an ordinary scratch container, and this module's
    whole contract is that a cache never fails a run.
    """
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "pnix" / "refs.json"
    try:
        return Path.home() / ".cache" / "pnix" / "refs.json"
    except RuntimeError:
        return None


def open_default() -> Cache | None:
    """A cache at the default location, or None if there is nowhere for one."""
    path = default_path()
    return None if path is None else Cache(path)


Key = tuple[str, tuple[str, ...]]
Rows = list[list[str]]


class Cache:
    """Keyed on the whole `ls-remote` call, which is the only honest key.

    `(url, patterns)` rather than `(url, ref)` because `resolve`, `resolve_tag`
    and `tags` ask different questions of the same URL, and because caching the
    *rows* rather than a chosen rev keeps the picking and peeling logic on the
    cached path identical to the live one: a hit cannot change how an answer is
    interpreted, only where the bytes came from.
    """

    def __init__(self, path: Path, ttl: int = TTL):
        self.path = Path(path)
        self.ttl = ttl
        #: hashed key -> age in seconds, for every entry a hit was served from.
        self.ages: dict[str, float] = {}
        self._entries: dict[str, dict] = self._read()
        self._dirty = False
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict]:
        try:
            doc = json.loads(self.path.read_text())
        except (OSError, ValueError):
            # Missing, unreadable, or not JSON. A cache is never worth failing a
            # run over.
            return {}
        if not isinstance(doc, dict) or doc.get("version") != VERSION:
            return {}
        entries = doc.get("entries")
        return entries if isinstance(entries, dict) else {}

    @staticmethod
    def _hash(key: Key) -> str:
        url, patterns = key
        raw = "\n".join((url, *patterns))
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def get(self, key: Key) -> Rows | None:
        with self._lock:
            entry = self._entries.get(self._hash(key))
            if not isinstance(entry, dict):
                return None
            at, rows = entry.get("at"), entry.get("rows")
            if not isinstance(at, (int, float)) or not isinstance(rows, list):
                return None
            age = time.time() - at
            # A negative age means the clock moved backwards since the entry was
            # written; trusting it would pin the rev for the TTL plus the jump.
            if age < 0 or age > self.ttl:
                return None
            self.ages[self._hash(key)] = age
            return rows

    def put(self, key: Key, rows: Rows) -> None:
        with self._lock:
            self._entries[self._hash(key)] = {"at": time.time(), "rows": rows}
            self._dirty = True

    def save(self) -> None:
        """Write once, atomically, and only if something changed.

        The pool has eight threads and they all share this object, so the file is
        read before they start and written after they finish rather than locked
        per access. `os.replace` is atomic on POSIX, so a crash mid-write leaves
        the old cache rather than a truncated one.
        """
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"version": VERSION, "entries": self._entries}
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".refs-")
            with os.fdopen(fd, "w") as f:
                json.dump(doc, f)
            os.replace(tmp, self.path)
            tmp = None
        except OSError:
            # An unwritable cache directory is not a reason to fail an update.
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
