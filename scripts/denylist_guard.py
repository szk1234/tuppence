"""Fail if any tracked or untracked-but-not-ignored file mentions a denylisted term.

The denylist is never stored in this repository. Supply it via:
  TUPPENCE_DENYLIST        newline-separated terms (e.g. a CI secret)
  TUPPENCE_DENYLIST_FILE   path to a file with one term per line
  git config tuppence.denylistFile <path>   (local, per clone)
Blank lines and lines starting with '#' are ignored. Matching is
case-insensitive on word boundaries. Matched terms are never printed —
only their 1-based index — so CI logs can't leak the list.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path


def _git_config_file(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "config", "--get", "tuppence.denylistFile"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    return value or None


def load_terms(env: Mapping[str, str], git_config_file: str | None) -> list[str]:
    raw = env.get("TUPPENCE_DENYLIST", "")
    for candidate in (env.get("TUPPENCE_DENYLIST_FILE"), git_config_file):
        if candidate and Path(candidate).is_file():
            raw += "\n" + Path(candidate).read_text(encoding="utf-8")
    terms: list[str] = []
    for line in raw.splitlines():
        term = line.strip()
        if term and not term.startswith("#") and term not in terms:
            terms.append(term)
    return terms


def compile_terms(terms: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(r"(?<!\w)" + re.escape(t) + r"(?!\w)", re.IGNORECASE) for t in terms]


def scan_text(text: str, patterns: list[re.Pattern[str]]) -> list[tuple[int, int]]:
    hits: list[tuple[int, int]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for index, pattern in enumerate(patterns):
            if pattern.search(line):
                hits.append((lineno, index))
    return hits


def redact(text: str, patterns: list[re.Pattern[str]]) -> str:
    for pattern in patterns:
        text = pattern.sub("[redacted]", text)
    return text


def candidate_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    return sorted({p for p in out.decode("utf-8").split("\0") if p})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root to scan")
    parser.add_argument("--require", action="store_true", help="fail if no terms are configured")
    parser.add_argument(
        "--no-git-config", action="store_true", help="ignore git config tuppence.denylistFile"
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    git_file = None if args.no_git_config else _git_config_file(root)
    terms = load_terms(os.environ, git_file)
    if not terms:
        print("denylist: no terms configured", file=sys.stderr)
        return 2 if args.require else 0

    patterns = compile_terms(terms)
    failures = 0
    for rel in candidate_files(root):
        shown = redact(rel, patterns)
        for _, index in scan_text(rel, patterns):
            print(f"{shown}: path matches denylisted term #{index + 1}")
            failures += 1
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for lineno, index in scan_text(text, patterns):
            print(f"{shown}:{lineno}: matches denylisted term #{index + 1}")
            failures += 1
    if failures:
        print(
            f"denylist: {failures} match(es) — remove personal data before committing",
            file=sys.stderr,
        )
        return 1
    print(f"denylist: clean ({len(terms)} terms checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
