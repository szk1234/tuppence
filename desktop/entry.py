"""PyInstaller entry point for the desktop app."""

import sys

from tuppence.cli import main

if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(main(["desktop", *args]))
