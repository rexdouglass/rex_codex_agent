"""Enable `python -m rex_codex`."""

from __future__ import annotations

from .cli import app


def main() -> None:  # pragma: no cover - exercised via Typer
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
