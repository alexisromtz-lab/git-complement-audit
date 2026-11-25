"""Entry point for ``python -m git_complement_audit``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
