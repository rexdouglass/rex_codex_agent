"""Module entry-point so `python -m hello` mirrors CLI execution."""

from __future__ import annotations

from . import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
