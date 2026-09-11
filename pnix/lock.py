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
"""

import json
from pathlib import Path

SCHEMA = 3


class SchemaError(Exception):
    pass


def read(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    doc = json.loads(path.read_text())
    version = doc.get("schema")
    if version != SCHEMA:
        raise SchemaError(
            f"{path}: lock schema {version} but this pnix speaks {SCHEMA}"
        )
    return doc.get("pins", {})


def write(path: Path, pins: dict[str, dict]) -> None:
    doc = {"schema": SCHEMA, "pins": {k: pins[k] for k in sorted(pins)}}
    path.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n")
