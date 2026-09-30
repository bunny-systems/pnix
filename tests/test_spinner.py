"""The spinner: issue #2, "add spinny :3".

Not decoration. `Progress.fetching` currently hides which pins are in flight
behind `-v`, because eight threads each printing a "starting" line buries the
results, which are what the command is for. A line that redraws in place shows
the same information without competing for scrollback -- so the spinner replaces
a compromise rather than adding a flourish.

What these guard is everything that is not the animation: that it stays off when
nobody is watching, that it never eats a result line, and that it cannot leave
the terminal in a mess.
"""

import io
import re
import threading

from pnix import spinner


class FakeTTY(io.StringIO):
    def __init__(self, tty: bool = True):
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_it_stays_silent_when_stderr_is_not_a_tty():
    """A pipe or a CI log gets no escape sequences at all."""
    out = FakeTTY(tty=False)
    s = spinner.Spinner(out)
    s.start()
    s.update({"nixpkgs"})
    s.tick()
    s.stop()
    assert out.getvalue() == ""


def test_it_draws_on_a_tty():
    out = FakeTTY()
    s = spinner.Spinner(out)
    s.start()
    s.update({"nixpkgs"})
    s.tick()
    s.stop()
    assert "nixpkgs" in out.getvalue()


def test_a_result_line_is_not_swallowed_by_the_animation():
    """The spinner owns the last line, so anything else printed has to clear it
    first and let the next tick redraw. Losing a result to a redraw is the one
    failure that would make the command worse than before."""
    out = FakeTTY()
    s = spinner.Spinner(out)
    s.start()
    s.update({"nixpkgs"})
    s.tick()
    s.write_line("  [1/3] nixpkgs  new  7a0f122f")
    s.stop()
    value = out.getvalue()
    assert "[1/3] nixpkgs  new  7a0f122f\n" in value


def test_stopping_clears_the_line_it_owned():
    out = FakeTTY()
    s = spinner.Spinner(out)
    s.start()
    s.update({"a"})
    s.tick()
    s.stop()
    assert out.getvalue().endswith("\r\x1b[2K")


def test_stopping_twice_is_harmless():
    out = FakeTTY()
    s = spinner.Spinner(out)
    s.start()
    s.stop()
    s.stop()


def test_it_names_what_is_in_flight_and_how_many_are_done():
    out = FakeTTY()
    s = spinner.Spinner(out, total=3)
    s.start()
    s.update({"hjem", "finix"}, done=1)
    s.tick()
    s.stop()
    value = out.getvalue()
    assert "finix" in value and "hjem" in value
    assert "1/3" in value


def test_many_in_flight_names_are_abbreviated_not_wrapped():
    """Eight workers on narrow terminal is a wrapped line that never erases
    cleanly, because `\\r` only returns to the start of the last row."""
    out = FakeTTY()
    s = spinner.Spinner(out, total=20, width=40)
    s.start()
    s.update({f"pin-number-{i}" for i in range(8)}, done=2)
    s.tick()
    s.stop()
    # Escape sequences occupy no columns, so they must come off before measuring.
    printable = [re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", part)
                 for part in out.getvalue().split("\r")]
    longest = max(len(line) for line in printable)
    assert longest <= 40, longest


def test_the_frame_advances_between_ticks():
    out = FakeTTY()
    s = spinner.Spinner(out)
    s.start()
    s.update({"a"})
    s.tick()
    first = out.getvalue()
    s.tick()
    s.stop()
    assert out.getvalue() != first * 2


def test_concurrent_writers_do_not_interleave():
    """Eight threads finish at once; each result must land as a whole line."""
    out = FakeTTY()
    s = spinner.Spinner(out, total=8)
    s.start()
    s.update({"x"})

    def worker(i):
        s.write_line(f"result-{i}-end")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    s.stop()
    for i in range(8):
        assert f"result-{i}-end\n" in out.getvalue()


def test_quiet_suppresses_lines_but_look_still_animates():
    """`look` passes quiet=True because it prints its own report, not per-pin
    lines -- but a cold `look` is 35 s of silence, which is the case issue #2 is
    actually about. So the animation is a separate decision from the lines."""
    from pnix import cli

    p = cli.Progress(3, quiet=True, animate=True)
    assert p.quiet
    assert p.spinner is not None
    p = cli.Progress(3, quiet=True, animate=False)
    assert p.spinner.enabled is False
