#!/usr/bin/env bash
# Deterministic self-development loop with two feature cards.
set -Eeuo pipefail

this_dir="$(dirname "${BASH_SOURCE[0]}")"
repo_root="$(git -C "$this_dir/.." rev-parse --show-toplevel 2>/dev/null || realpath "$this_dir/..")"
workspace="$repo_root/.selftest_workspace"
log_file="$workspace/selftest.log"
fake_codex="$repo_root/bin/fake-codex"

if [[ ! -x "$fake_codex" ]]; then
  echo "[!] Missing executable Codex stub at $fake_codex" >&2
  exit 1
fi

if [[ -d "$workspace" ]]; then
  rm -rf "$workspace"
fi
mkdir -p "$workspace"
cd "$workspace"
shim_dir="$workspace/.shim"
mkdir -p "$shim_dir"
ln -sf "$(command -v python3)" "$shim_dir/python"
export PATH="$shim_dir:$PATH"
export PYTHONPATH="$workspace/src:${PYTHONPATH:-}"
export ROOT="$workspace"
export PYTHONHASHSEED=0

last_status=0

run() {
  printf '\n[%s] %s\n' "$(date -Ins --utc)" "$*" | tee -a "$log_file"
  set +e
  "$@" 2>&1 | tee -a "$log_file"
  status=${PIPESTATUS[0]}
  set -e
  if [[ $status -ne 0 ]]; then
    printf '[!] Command failed (%s) with exit status %s\n' "$*" "$status" | tee -a "$log_file"
    exit "$status"
  fi
  last_status=$status
}

finalized=0
finalize() {
  local exit_status=$?
  if ((finalized)); then
    return
  fi
  finalized=1
  set +e

  local audit_dir="$repo_root/for_external_GPT5_pro_audit"
  mkdir -p "$audit_dir"
  local latest_audit
  latest_audit="$(ls -1 "$audit_dir"/audit_*.md 2>/dev/null | sort | tail -1)"
  if [[ -z "$latest_audit" ]]; then
    latest_audit="$audit_dir/audit_$(date -u +%Y%m%d%H%M%S)_selftest.md"
    : >"$latest_audit"
  fi

  local status_output=""
  if [[ -x "$workspace/rex-codex" ]]; then
    status_output="$(cd "$workspace" && ./rex-codex status 2>&1 || true)"
  fi

  local specs_output=""
  if [[ -d "$workspace/tests/feature_specs" ]]; then
    specs_output="$(cd "$workspace" && find tests/feature_specs -type f -print | sort || true)"
  fi

  {
    printf '\n## Local Selftest Loop (%s UTC)\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf -- '- Workspace: %s\n' "$workspace"
    printf -- '- Features exercised: %s\n' "${SLUGS[*]:-n/a}"
    if [[ -n "${global_status:-}" ]]; then
      printf -- '- Global discriminator exit code: %s\n' "$global_status"
    fi
    printf -- '- Last command exit code: %s\n' "${last_status:-n/a}"
    printf -- '- Script exit code: %s\n\n' "$exit_status"
    if [[ -n "$status_output" ]]; then
      printf '### rex-codex status\n\n```\n%s\n```\n' "$status_output"
    fi
    if [[ -n "$specs_output" ]]; then
      printf '### Generated spec files\n\n```\n%s\n```\n' "$specs_output"
    fi
    if [[ -f "$log_file" ]]; then
      printf '### Command log\n\n```\n'
      cat "$log_file"
      printf '```\n'
    fi
    if [[ -f "$workspace/src/hello/__init__.py" ]]; then
      printf '### Runtime module (src/hello/__init__.py)\n\n```python\n'
      cat "$workspace/src/hello/__init__.py"
      printf '```\n'
    fi
    if [[ -f "$workspace/src/hello/__main__.py" ]]; then
      printf '### CLI entry (src/hello/__main__.py)\n\n```python\n'
      cat "$workspace/src/hello/__main__.py"
      printf '```\n'
    fi
  } >>"$latest_audit"

  cd "$repo_root" 2>/dev/null || true
  if [[ "${SELFTEST_KEEP:-0}" == "1" ]]; then
    echo "[i] Preserved workspace at $workspace"
  else
    rm -rf "$workspace"
    echo "[*] Removed workspace at $workspace"
  fi

  set -e
}
trap finalize EXIT

run git init -q
run git config user.email "selftest@rex.codex"
run git config user.name "Rex Codex Selftest"
printf "# selftest workspace\n" > README.md
mkdir -p src/hello
cat > src/hello/__init__.py <<'PY'
from __future__ import annotations

import argparse
from typing import Sequence

DEFAULT_MESSAGE = "Hello World"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print a configurable greeting.")
    parser.add_argument("--message", default=DEFAULT_MESSAGE, help="Override the greeting message.")
    parser.add_argument("--repeat", type=int, default=1, help="Number of times to print the greeting.")
    parser.add_argument("--quiet", action="store_true", help="Suppress output.")
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
run git add -A
run git commit -q -m "chore: seed runtime"

export REPO_URL="$repo_root"
export REX_AGENT_CHANNEL=main
export REX_AGENT_FORCE=1
export REX_AGENT_SKIP_DOCTOR=1

run bash "$repo_root/scripts/install.sh" --force --channel main

export CODEX_BIN="$fake_codex"
export REX_AGENT_NO_UPDATE=1
export PYTHON=python3
export PYENV_VERSION="${PYENV_VERSION:-3.11.8}"

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
  run ./rex-codex card new "$slug" \
    --title "${TITLES[$slug]}" \
    --summary "${SUMMARIES[$slug]}" \
    --acceptance "${ACCEPTANCE[$slug]}"

  run ./rex-codex generator "documents/feature_cards/${slug}.md" --single-pass
  run ./rex-codex discriminator --feature-only --single-pass --disable-llm
done

run ./rex-codex discriminator --global --single-pass --disable-llm
global_status=$last_status
