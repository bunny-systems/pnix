"""Read and write pins.lock.json.

The lock is the only file pnix writes. Declarations live in the consumer's
modules; nothing else on disk is generated.
"""

import json
from pathlib import Path

SCHEMA = 1


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
