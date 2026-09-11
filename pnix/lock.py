"""Read and write pins.lock.json.

The lock is the only file pnix writes. Declarations live in the consumer's
modules; nothing else on disk is generated.

Schema 2 (2026-09-11) adds the `fetch` discriminator and splits every pin
entry in two:

* **read by the vendored resolver, and therefore frozen by the schema** --
  `fetch`, `rev`, `narHash`, `lastModified`, `flake`, `dir`, `follows`,
  `excludeFollow`, and from schema 3 `patches` and `importable`;
* **provenance, read only by pnix itself** -- `type`, `host`, `owner`, `repo`,
  `url`, `ref`, `submodules`, `shallow`, `patches`, and whatever a future
  source type wants. These can change without a schema bump, because nothing
  in the consumer's repo looks at them.

Schema 3 (2026-09-11) adds `patches`. It is a bump rather than an additive
change precisely because the vendored resolver has to read it: a resolver that
predates patches would ignore the field and hand back an *unpatched* source
while the lock said otherwise -- silent, and wrong in the dangerous direction.
Each patch node carries `url` and `hash` for the resolver, and `head`/`base`/
`state`/`merged` as provenance for `pnix look`.

Schema 4 (2026-09-11) adds `lfs` and `exportIgnore` to a git `fetch` node. Same
rule as schema 3: a resolver predating them enumerates the fetchGit arguments it
knows and drops the rest, so it would hand back pointer files, or a tree that
disagrees with the tarball of the same rev, while the lock said otherwise --
silently wrong.

It should be the last bump of this shape. The fetchers are now flat
passthroughs: every field of a `fetch` node except `kind` and `hash` reaches the
builtin unchanged, so a future fetchGit option is a pure Python change that any
schema-4 resolver already forwards.
"""

import json
from pathlib import Path

SCHEMA = 4


class SchemaError(Exception):
    pass


def _add_fetch(pins: dict[str, dict]) -> dict[str, dict]:
    """1 -> 2. Derive the `fetch` discriminator each node gained in schema 2.

    Schema 1 keyed the resolver on `type`, which is exactly the information a
    source class turns into a `fetch` node, so this is recoverable rather than a
    reason to re-resolve.
    """
    from pnix import sources

    out = {}
    for name, node in pins.items():
        node = dict(node)
        if "fetch" not in node:
            src = sources.get(node.get("type", "github"))
            node["fetch"] = src.fetch_spec(node)
        out[name] = node
    return out


# from-version -> what it takes to become the next version.
#
# Most bumps need nothing. A schema bump means *an older resolver must not read
# a newer lock* -- it would drop a field it does not know and be silently wrong
# -- and that says nothing about whether an older lock is still valid data.
# Schema 3 added `patches` and 4 added `lfs`/`exportIgnore`; a node without them
# means "none", which is what it already meant.
MIGRATIONS = {
    1: _add_fetch,
    2: lambda pins: pins,
    3: lambda pins: pins,
}


def migrate(pins: dict[str, dict], version: int) -> dict[str, dict]:
    """Carry `pins` forward to SCHEMA, preserving every locked field."""
    while version < SCHEMA:
        step = MIGRATIONS.get(version)
        if step is None:
            raise SchemaError(
                f"no migration from lock schema {version} to {version + 1}"
            )
        pins = step(pins)
        version += 1
    return pins


def read_at(path: Path) -> tuple[dict[str, dict], int | None]:
    """Lock contents, plus the schema they came from if it was not current.

    A lock that predates this pnix is **migrated, never discarded.** Refusing it
    would leave deleting the lock as the only way forward, and re-resolving from
    declarations moves every pin that did not name an explicit `rev` to whatever
    is at the tip today -- so a schema bump could silently unpin a fleet. The
    revs, hashes and refs are the expensive part; the schema number is not.

    A lock from the *future* is still refused. Its fields may mean something
    this pnix does not know, and guessing is how you get a wrong tree.
    """
    if not path.exists():
        return {}, None
    doc = json.loads(path.read_text())
    version = doc.get("schema")

    if version == SCHEMA:
        return doc.get("pins", {}), None
    if not isinstance(version, int):
        raise SchemaError(
            f"{path}: no `schema` field. This is not a pnix lock, or it predates "
            f"schema 1. Move it aside and run `pnix update`."
        )
    if version > SCHEMA:
        raise SchemaError(
            f"{path}: lock schema {version} but this pnix speaks {SCHEMA}. "
            f"A newer pnix wrote it; upgrade pnix rather than downgrading the lock."
        )
    return migrate(doc.get("pins", {}), version), version


def read(path: Path) -> dict[str, dict]:
    pins, _ = read_at(path)
    return pins


def write(path: Path, pins: dict[str, dict]) -> None:
    # `pnix update` before `pnix init` is a legitimate order, and .pnix/
    # may not exist yet.
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"schema": SCHEMA, "pins": {k: pins[k] for k in sorted(pins)}}
    path.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n")
