"""Raw uploads, stored once under `files/statements/<sha256>.<ext>` (spec §3.3)."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from tuppence.ingest.sniff import EXTENSIONS
from tuppence.paths import make_private_dir

_SHA = re.compile(r"^[0-9a-f]{64}$")
_EXTS = {*EXTENSIONS.values(), "png", "jpg"}


class StatementFiles:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, sha256: str, ext: str) -> Path:
        if not _SHA.match(sha256) or ext not in _EXTS:
            raise ValueError("bad stored-file name")
        return self.root / f"{sha256}.{ext}"

    def save(self, data: bytes, ext: str) -> tuple[str, Path]:
        sha = hashlib.sha256(data).hexdigest()
        path = self.path_for(sha, ext)
        if not path.exists():
            make_private_dir(self.root)
            fd, name = tempfile.mkstemp(dir=self.root, suffix=".part")  # unique, 0600 on POSIX
            tmp = Path(name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
        return sha, path

    def delete(self, sha256: str, ext: str) -> None:
        self.path_for(sha256, ext).unlink(missing_ok=True)
