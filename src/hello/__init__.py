"""Toy hello-world CLI used by rex_codex self-tests."""

from __future__ import annotations

import argparse
from typing import Iterable, List, Optional

DEFAULT_MESSAGE = "Hello World"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit a friendly greeting from the CLI."
    )
    parser.add_argument(
        "--message",
        help="Override the default greeting text.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="Number of times to repeat the greeting (default: 1).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress all output while still returning a success exit code.",
    )
    return parser


def build_greeting(message: str, repeat: int) -> str:
    lines = [message] * repeat
    return "\n".join(lines) + "\n"


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    message = args.message or DEFAULT_MESSAGE
    if args.repeat <= 0:
        parser.error("--repeat must be a positive integer")
    greeting = build_greeting(message, args.repeat)

    if not args.quiet:
        # Use stdout.write to avoid an extra newline after the joined output.
        import sys

        sys.stdout.write(greeting)
        sys.stdout.flush()
    return 0


__all__ = ["DEFAULT_MESSAGE", "build_parser", "build_greeting", "main"]
