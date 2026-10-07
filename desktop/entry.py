"""PyInstaller entry point for the desktop app."""

import multiprocessing
import os
import sys


def ensure_std_streams() -> None:
    """Windowed builds have no stdout/stderr; give libraries a writable sink."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")  # noqa: SIM115


if __name__ == "__main__":
    multiprocessing.freeze_support()
    ensure_std_streams()
    from tuppence.cli import main

    sys.exit(main(["desktop", *sys.argv[1:]]))
