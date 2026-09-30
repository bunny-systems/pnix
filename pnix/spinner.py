"""A one-line status display that redraws in place.

Issue #2. `pnix update` is ~20 s of network with nothing on screen, which is
indistinguishable from a hang, and `Progress` answers that by printing each pin
as it lands. What it could not show is which pins are *in flight*: eight threads
each printing a "starting" line buries the results, so that information sits
behind `-v` and is rarely seen. A line that redraws costs no scrollback, so it
can carry the in-flight names for free.

Three things matter more here than the animation:

* **Off when nobody is watching.** A pipe, a CI log or `-q` gets no escape
  sequences. `\\r` and `\\x1b[2K` in a log file are noise that outlives the run.
* **It must never eat a result.** The spinner owns the last line, so every other
  write has to clear that line first and let the next tick redraw. A result lost
  to a redraw would make the command worse than it was.
* **It must not wrap.** `\\r` returns to the start of the last screen row, so a
  line longer than the terminal cannot be erased -- it leaves debris that stays
  after the run. Everything is truncated to the terminal width instead.
"""

import os
import shutil
import sys
import threading

#: Braille dots, the same cycle tack animates. Any single-width run works; these
#: are one cell each so the line length is predictable.
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

#: Wipe from the start of the line: carriage return, then erase-to-end.
CLEAR = "\r\x1b[2K"


def terminal_width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except OSError:
        return default


class Spinner:
    """Owns the last line of `stream` between `start()` and `stop()`.

    Inert unless `stream` is a tty, so a caller can construct one
    unconditionally and let it decide whether to draw.
    """

    def __init__(self, stream=None, total: int = 0, width: int | None = None):
        self.stream = stream if stream is not None else sys.stderr
        self.total = total
        self.width = width if width is not None else terminal_width()
        self._frame = 0
        self._inflight: set[str] = set()
        self._done = 0
        self._drawn = False
        self._running = False
        self._lock = threading.RLock()
        try:
            self.enabled = bool(self.stream.isatty()) and not os.environ.get("NO_COLOR")
        except (AttributeError, ValueError):
            self.enabled = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            self._running = True

    def stop(self) -> None:
        """Erase the owned line and stop drawing. Safe to call twice."""
        with self._lock:
            if self._running and self._drawn:
                self.stream.write(CLEAR)
                self._flush()
            self._drawn = False
            self._running = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- state -------------------------------------------------------------

    def update(self, inflight, done: int | None = None) -> None:
        with self._lock:
            self._inflight = set(inflight)
            if done is not None:
                self._done = done

    # -- drawing -----------------------------------------------------------

    def _text(self) -> str:
        counter = f"{self._done}/{self.total}" if self.total else str(self._done)
        names = ", ".join(sorted(self._inflight))
        line = f"{FRAMES[self._frame % len(FRAMES)]} {counter}"
        if names:
            line = f"{line}  {names}"
        # Truncated rather than wrapped: `\r` cannot reach a previous row, so a
        # wrapped line is one that can never be erased.
        limit = max(self.width - 1, 1)
        if len(line) > limit:
            line = line[: max(limit - 1, 1)] + "…"
        return line

    def tick(self) -> None:
        with self._lock:
            if not (self.enabled and self._running):
                return
            self.stream.write(CLEAR + self._text())
            self._flush()
            self._drawn = True
            self._frame += 1

    def write_line(self, text: str) -> None:
        """Print a line above the spinner, atomically.

        Clear, write the line with its newline, and leave the spinner undrawn so
        the next tick puts it back underneath. Holding the lock across all of it
        is what keeps eight finishing threads from interleaving.
        """
        with self._lock:
            if self.enabled and self._drawn:
                self.stream.write(CLEAR)
                self._drawn = False
            self.stream.write(text + "\n")
            self._flush()

    def _flush(self) -> None:
        try:
            self.stream.flush()
        except (AttributeError, ValueError):
            pass
