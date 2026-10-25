"""Allow ``python -m rootcause.evals.manufacturing``."""

from __future__ import annotations

import sys

from rootcause.evals.manufacturing.cli import main

if __name__ == "__main__":  # pragma: no cover - exercised via ``cli.main`` in tests
    sys.exit(main(sys.argv[1:]))
