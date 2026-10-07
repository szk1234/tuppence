"""Build the desktop bundle with PyInstaller, then smoke-test the frozen binary."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def binary_path() -> Path:
    base = ROOT / "dist" / "Tuppence"
    if sys.platform == "win32":
        return base / "Tuppence.exe"
    if sys.platform == "darwin":
        return ROOT / "dist" / "Tuppence.app" / "Contents" / "MacOS" / "Tuppence"
    return base / "Tuppence"


def main() -> int:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "desktop/tuppence.spec", "--noconfirm", "--clean"],
        cwd=ROOT,
        check=True,
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "smoke.json"
        data = Path(tmp) / "data dir ü"
        subprocess.run(
            [str(binary_path()), "--smoke", "--smoke-out", str(out), "--data-dir", str(data)],
            check=True,
            timeout=120,
        )
        result = json.loads(out.read_text(encoding="utf-8"))
    if not result.get("ok"):
        print(f"desktop smoke: FAILED {result}")
        return 1
    print(f"smoke: ok desktop {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
