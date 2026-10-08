"""Refuse a release tag that doesn't match the package version.

    uv run python scripts/check_release_version.py v0.2.0-dev.1

Tags read the way people write them (v0.2.0-dev.1, v0.2.0-rc.1, v1.0.0); the package uses
PEP 440 (0.2.0.dev1, 0.2.0rc1, 1.0.0). Both pyproject.toml and tuppence.__version__ must match.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TAG = re.compile(r"^v(\d+\.\d+\.\d+)(?:-(dev|alpha|beta|rc)\.(\d+))?$")
_PEP440 = {"dev": ".dev", "alpha": "a", "beta": "b", "rc": "rc"}


def pep440(tag: str) -> str:
    match = _TAG.match(tag)
    if not match:
        raise ValueError(f"{tag!r} isn't a release tag like v0.2.0-dev.1 or v1.0.0")
    base, stage, number = match.groups()
    return base if stage is None else f"{base}{_PEP440[stage]}{number}"


def package_versions(root: Path = ROOT) -> tuple[str, str]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    init = (root / "src" / "tuppence" / "__init__.py").read_text(encoding="utf-8")
    found = re.search(r'^__version__ = "([^"]+)"', init, re.MULTILINE)
    return project["version"], found.group(1) if found else ""


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: check_release_version.py <tag>", file=sys.stderr)
        return 2
    try:
        wanted = pep440(args[0])
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    project, module = package_versions()
    if project != wanted or module != wanted:
        print(
            f"Tag {args[0]} means version {wanted}, but pyproject.toml says {project} and"
            f" tuppence.__version__ says {module}.",
            file=sys.stderr,
        )
        return 1
    print(f"Tag {args[0]} matches version {wanted}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
