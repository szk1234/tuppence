"""Fail if any tracked or untracked-but-not-ignored file mentions a denylisted term.

The denylist is never stored in this repository. Supply it via:
  TUPPENCE_DENYLIST        newline-separated terms (e.g. a CI secret)
  TUPPENCE_DENYLIST_FILE   path to a file with one term per line
  git config tuppence.denylistFile <path>   (local, per clone)
Blank lines and lines starting with '#' are ignored. Matching is
case-insensitive on word boundaries. Matched terms are never printed —
only their 1-based index — so CI logs can't leak the list.

If a denylist file is configured (env var or git config) but cannot be read,
the guard fails closed (exit 2) rather than silently checking nothing.

Modes (default scans the working tree):
  --staged             scan the index (staged content and paths)
  --message-file PATH  scan a commit message file
  --range "A..B"       scan every commit (message and patch) in a rev range
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
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


class DenylistFileError(Exception):
    """A configured denylist file is missing or unreadable (never carries the path)."""


def load_terms(env: Mapping[str, str], git_config_file: str | None) -> list[str]:
    raw = env.get("TUPPENCE_DENYLIST", "")
    for candidate in (env.get("TUPPENCE_DENYLIST_FILE"), git_config_file):
        if not candidate:
            continue
        try:
            raw += "\n" + Path(candidate).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise DenylistFileError from None
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
    """Redact every term in one pass, longest term first, so overlaps leave no fragment."""
    if not patterns:
        return text
    ordered = sorted((p.pattern for p in patterns), key=len, reverse=True)
    combined = re.compile("|".join(f"(?:{p})" for p in ordered), re.IGNORECASE)
    return combined.sub("[redacted]", text)


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout


def candidate_files(root: Path) -> list[str]:
    out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted({p for p in out.decode("utf-8").split("\0") if p})


def staged_files(root: Path) -> list[str]:
    out = _git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return sorted({p for p in out.decode("utf-8").split("\0") if p})


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="ignore")


def _scan_paths_and_contents(
    names: list[str],
    read: Callable[[str], bytes | None],
    patterns: list[re.Pattern[str]],
) -> int:
    failures = 0
    for rel in names:
        shown = redact(rel, patterns)
        for _, index in scan_text(rel, patterns):
            print(f"{shown}: path matches denylisted term #{index + 1}")
            failures += 1
        data = read(rel)
        if data is None:
            continue
        for lineno, index in scan_text(_decode(data), patterns):
            print(f"{shown}:{lineno}: matches denylisted term #{index + 1}")
            failures += 1
    return failures


def scan_worktree(root: Path, patterns: list[re.Pattern[str]]) -> int:
    def read(rel: str) -> bytes | None:
        path = root / rel
        return path.read_bytes() if path.is_file() else None

    return _scan_paths_and_contents(candidate_files(root), read, patterns)


def scan_staged(root: Path, patterns: list[re.Pattern[str]]) -> int:
    return _scan_paths_and_contents(
        staged_files(root), lambda rel: _git(root, "show", f":{rel}"), patterns
    )


def scan_message_file(path: Path, patterns: list[re.Pattern[str]]) -> int:
    failures = 0
    for lineno, index in scan_text(_decode(path.read_bytes()), patterns):
        print(f"commit message:{lineno}: matches denylisted term #{index + 1}")
        failures += 1
    return failures


_RANGE_TOKENS = {"--not", "--remotes"}


def scan_range(root: Path, rev_range: str, patterns: list[re.Pattern[str]]) -> int:
    """Scan message and patch of every commit in the range; print only sha and term index."""
    tokens = rev_range.split()
    if not tokens or any(t.startswith("-") and t not in _RANGE_TOKENS for t in tokens):
        raise ValueError("invalid range")
    shas = _git(root, "rev-list", *tokens).decode("utf-8").split()
    failures = 0
    for sha in shas:
        text = _decode(_git(root, "show", "-m", "--no-color", "--format=%B", sha))
        for index in sorted({idx for _, idx in scan_text(text, patterns)}):
            print(f"commit {sha[:10]}: matches denylisted term #{index + 1}")
            failures += 1
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root to scan")
    parser.add_argument("--require", action="store_true", help="fail if no terms are configured")
    parser.add_argument(
        "--no-git-config", action="store_true", help="ignore git config tuppence.denylistFile"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="scan the index, not the worktree")
    mode.add_argument("--message-file", metavar="PATH", help="scan a commit message file")
    mode.add_argument("--range", metavar="REVS", help='scan commits in a range, e.g. "A..B"')
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    git_file = None if args.no_git_config else _git_config_file(root)
    try:
        terms = load_terms(os.environ, git_file)
    except DenylistFileError:
        print("denylist: configured denylist file not found", file=sys.stderr)
        return 2
    if not terms:
        print("denylist: no terms configured", file=sys.stderr)
        return 2 if args.require else 0

    patterns = compile_terms(terms)
    try:
        if args.staged:
            failures = scan_staged(root, patterns)
        elif args.message_file:
            failures = scan_message_file(Path(args.message_file), patterns)
        elif args.range:
            failures = scan_range(root, args.range, patterns)
        else:
            failures = scan_worktree(root, patterns)
    except (subprocess.CalledProcessError, OSError, ValueError):
        print("denylist: scan failed; refusing to pass", file=sys.stderr)
        return 2
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
