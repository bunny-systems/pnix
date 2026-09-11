"""Run the Nix collector and parse its output.

Nix cannot write files, so the collector's result comes back over stdout as
JSON and lives only in this process. There is no generated manifest: the
declarations in the consumer's modules are the source of truth, and the
lock is the only artifact.
"""

import json
import subprocess
from pathlib import Path

from pnix.prefetch import NO_EXPERIMENTAL


class CollectError(Exception):
    pass


# collect.nix ships with the CLI rather than being vendored into consumers:
# it runs only at lock time, so bundling it here means it can never drift from
# the code that invokes it. Plain __file__ rather than importlib.resources
# because a Nix-installed Python package is always unzipped on disk.
NIXSRC = Path(__file__).resolve().parent / "nixsrc"


def collect(files: list[Path], attr: str = "pins") -> tuple[dict, dict, list[str]]:
    """Evaluate the bundled collector over an explicit file list.

    Returns (pins, provenance, skipped). `skipped` lists candidates whose
    result could not be forced with stubbed arguments -- package expressions,
    almost always, which match the grep by coincidence. Measured on nixpkgs:
    56 candidates out of 44,024 files, of which 49 are skipped this way.

    `files` comes from pnix.discover.candidates. Passing it explicitly is what
    keeps collection safe and O(candidates) rather than O(tree): Nix never
    imports a file that could not possibly declare a pin.

    --strict is load-bearing, not a speed knob: it forces every pin value, so
    a declaration that reads one of its file's arguments fails here with
    PASS1-FORCED-PIN instead of being written into the lock as a thunk-shaped
    hole nobody looks at.
    """
    if not files:
        return {}, {}, []

    expr = "[ " + " ".join(str(Path(f).resolve()) for f in files) + " ]"
    proc = subprocess.run(
        ["nix-instantiate", "--eval", "--strict", "--json",
         str(NIXSRC / "collect.nix"),
         "--arg", "files", expr,
         "--argstr", "attr", attr,
         *NO_EXPERIMENTAL],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise CollectError(_blame(files, attr, proc.stderr.strip()))
    doc = json.loads(proc.stdout)
    return doc["pins"], doc["provenance"], doc.get("skipped", [])


def _blame(files: list[Path], attr: str, stderr: str) -> str:
    """Name the candidate that killed the run.

    The collector's WHNF probe catches `throw`, which is what argument stubs
    raise, but not every failure: a missing `import` path raises an IO error
    that `tryEval` does not catch, and neither does it catch `{ }.nope` or
    calling a non-function. Those abort the whole evaluation with a Nix stack
    trace that names collect.nix rather than the offending file.

    Only ever runs on the error path, so bisecting one file at a time is fine.
    """
    if len(files) < 2:
        return stderr
    culprits = []
    for f in files:
        try:
            collect([f], attr=attr)
        except CollectError:
            culprits.append(str(Path(f).resolve()))
    if not culprits:
        return stderr
    named = "\n".join(f"  {c}" for c in culprits)
    return (
        f"{stderr}\n\n"
        f"pnix: the candidate(s) below could not be evaluated at all, which "
        f"aborts collection:\n{named}\n"
        f"If a file here is not meant to declare pins, scope the scan with "
        f"--root, or move it into a hidden directory -- those are never "
        f"walked. The collector cannot skip it for you: its probe uses "
        f"tryEval, which catches a `throw` but not an undefined variable or a "
        f"missing import."
    )
