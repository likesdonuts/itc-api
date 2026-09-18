"""Backwards-compatible shortcut for the full pipeline.

Equivalent to `python cli.py refresh`: run new case discovery, update every
case already on disk, then re-render the site. Use cli.py directly when you
want one of those steps on its own.
"""

from __future__ import annotations

import sys

from cli import main

if __name__ == "__main__":
    sys.exit(main(["refresh", *sys.argv[1:]]))
