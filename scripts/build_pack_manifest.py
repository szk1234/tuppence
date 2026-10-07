"""Write manifest.json (version, licence, SHA-256 of each file) for the uk-banks pack.

uv run python scripts/build_pack_manifest.py src/tuppence/datapacks/baseline/uk-banks
"""

import hashlib
import json
import sys
from pathlib import Path


def main() -> None:
    folder = Path(sys.argv[1])
    files = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob("*.yaml"))
    }
    manifest = {
        "id": "uk-banks",
        "version": "2026.10.0",
        "published": "2026-10-07",
        "licence": "CC0-1.0",
        "sources": ["Community contributions. Every layout notes whether it is confirmed."],
        "files": files,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(files, indent=2))


if __name__ == "__main__":
    main()
