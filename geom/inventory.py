"""Face inventory + hash-keyed cache (Task 2).

FaceInventory is the JSON-serializable unit the query engine (Task 6) and
viewer backend (Task 8) consume. Cached keyed by the source file's sha256:
face tags are only stable per exact file content, so the hash is the identity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from geom.parser import FaceRecord, parse_step

DEFAULT_CACHE_DIR = Path(".sim_intent_cache") / "inventories"


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class FaceInventory:
    source_name: str
    file_sha256: str
    faces: list[FaceRecord]

    def face(self, tag: int) -> FaceRecord:
        for record in self.faces:
            if record.tag == tag:
                return record
        raise KeyError(f"no face with tag {tag}")

    def to_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "file_sha256": self.file_sha256,
            "faces": [f.to_dict() for f in self.faces],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FaceInventory":
        return cls(
            source_name=data["source_name"],
            file_sha256=data["file_sha256"],
            faces=[FaceRecord.from_dict(f) for f in data["faces"]],
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "FaceInventory":
        return cls.from_dict(json.loads(text))


def get_inventory(
    path: str | Path, cache_dir: str | Path = DEFAULT_CACHE_DIR
) -> tuple[FaceInventory, bool]:
    """Load (or parse and cache) the face inventory for a STEP file.

    Returns (inventory, cache_hit).
    """
    path = Path(path)
    cache_dir = Path(cache_dir)
    sha = file_sha256(path)
    cache_file = cache_dir / f"{sha}.json"

    if cache_file.is_file():
        return FaceInventory.from_json(cache_file.read_text(encoding="utf-8")), True

    inventory = FaceInventory(
        source_name=path.name, file_sha256=sha, faces=parse_step(path)
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(inventory.to_json(), encoding="utf-8")
    return inventory, False
