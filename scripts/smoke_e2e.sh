#!/usr/bin/env bash
# Deterministic end-to-end smoke of rex_codex_agent using the local checkout and Codex stub.
set -Eeuo pipefail

this_dir="$(dirname "${BASH_SOURCE[0]}")"
repo_root="$(git -C "$this_dir/.." rev-parse --show-toplevel 2>/dev/null || realpath "$this_dir/..")"
fake_codex="$repo_root/bin/fake-codex"

if [[ ! -x "$fake_codex" ]]; then
  echo "[!] Missing executable Codex stub at $fake_codex" >&2
  exit 1
fi

workdir="$(mktemp -d -t rex-codex-smoke.XXXXXX)"
keep="${KEEP:-0}"
cleanup() {
  status=$?
  if [[ "$keep" == "1" ]]; then
    echo "[i] Kept workdir: $workdir"
  else
    rm -rf "$workdir"
  fi
  exit $status
}
trap cleanup EXIT

echo "[*] Workdir: $workdir"
cd "$workdir"

mkdir dummy && cd dummy
git init -q
git config user.email "smoke@test.local"
git config user.name "Rex Codex Smoke"
printf "# dummy project\n" > README.md
shim_dir="$PWD/.shim"
mkdir -p "$shim_dir"
ln -sf "$(command -v python3)" "$shim_dir/python"
export PATH="$shim_dir:$PATH"
mkdir -p src/hello
cat > src/hello/__init__.py <<'PY'
from __future__ import annotations

from collections.abc import Sequence

import argparse

DEFAULT_MESSAGE = "Hello World"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print a configurable greeting."
    )
    parser.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help="Override the greeting message.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Number of times to print the greeting.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repeats = args.repeat if args.repeat >= 0 else 0
    if not args.quiet and repeats > 0:
        for _ in range(repeats):
            print(args.message)
    return 0


def run() -> int:
    return main(None)
PY
cat > src/hello/__main__.py <<'PY'
from __future__ import annotations

from . import main


def entrypoint() -> int:
    return main(None)


if __name__ == "__main__":
    raise SystemExit(entrypoint())
PY
git add -A
git commit -m "chore: seed dummy runtime" >/dev/null

export REPO_URL="$repo_root"
export REX_AGENT_CHANNEL=main
export REX_AGENT_FORCE=1
export REX_AGENT_SKIP_DOCTOR=1

bash "$repo_root/scripts/install.sh" --force --channel main

declare -a SLUGS=("hello_greet" "hello_cli")
declare -A TITLES
declare -A SUMMARIES
declare -A ACCEPTANCE

TITLES["hello_greet"]="Print a default greeting"
SUMMARIES["hello_greet"]="Ensure `python -m hello` prints 'Hello World'."
ACCEPTANCE["hello_greet"]="python -m hello outputs Hello World once"

TITLES["hello_cli"]="Configure greeting via CLI"
SUMMARIES["hello_cli"]="Support --message, --repeat, and --quiet flags for the hello app."
ACCEPTANCE["hello_cli"]="python -m hello --message 'Hi' --repeat 2 prints Hi twice"

for slug in "${SLUGS[@]}"; do
  ./rex-codex card new "$slug" \
    --title "${TITLES[$slug]}" \
    --summary "${SUMMARIES[$slug]}" \
    --acceptance "${ACCEPTANCE[$slug]}"
done

export PYTHON=python3
export CODEX_BIN="$fake_codex"
export REX_AGENT_NO_UPDATE=1
export PYENV_VERSION="${PYENV_VERSION:-3.11.8}"

./rex-codex loop --feature-only --no-self-update --tail 120
./rex-codex discriminator --global --single-pass --disable-llm

echo
echo "[✓] Smoke run complete."
echo "    Created spec files:"
find tests/feature_specs -maxdepth 3 -type f -print || true
echo
./rex-codex status
