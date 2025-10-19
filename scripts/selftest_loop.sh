#!/usr/bin/env bash
# End-to-end self-development loop that exercises two feature cards with the real Codex CLI.
set -Eeuo pipefail

this_dir="$(dirname "${BASH_SOURCE[0]}")"
repo_root="$(git -C "$this_dir/.." rev-parse --show-toplevel 2>/dev/null || realpath "$this_dir/..")"
workspace="$repo_root/.selftest_workspace"
log_file="$workspace/selftest.log"
monitor_log_dir="$workspace/.agent/logs"
monitor_events_file="$monitor_log_dir/events.jsonl"
monitor_port_file="$monitor_log_dir/monitor.port"
monitor_url=""
monitor_port=""
progress_total=1
progress_step=0

emit_monitor_event() {
  local phase="${1:-selftest}"
  local type="${2:-stage}"
  local message="${3:-}"
  local status="${4:-running}"
  local progress="${5:-}"
  local level="${6:-}"
  local slug="${7:-selftest_loop}"

  if [[ -z "$monitor_log_dir" ]]; then
    return
  fi

  local python_code
  read -r -d '' python_code <<'PY' || true
import json
import os
import sys

phase, type_, message, status, progress, level, slug = sys.argv[1:8]

def maybe_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None

try:
    from rex_codex.scope_project.events import emit_event
except Exception as exc:
    sys.stderr.write(f"[selftest-loop] Failed to emit monitor event: {exc}\n")
    raise SystemExit(0)

data: dict[str, object] = {"task": slug or "selftest_loop"}
if message:
    data["message"] = message
if status:
    data["status"] = status
progress_value = maybe_float(progress)
if progress_value is not None:
    data["progress"] = progress_value
if level:
    data["level"] = level

emit_event(phase or "selftest", type_ or "stage", slug=slug or "selftest_loop", **data)
PY

  PYTHONPATH="$repo_root/src" \
  ROOT="$workspace" \
  LOG_DIR="$monitor_log_dir" \
  REX_MONITOR_EVENTS_FILE="$monitor_events_file" \
  python3 - "$phase" "$type" "$message" "$status" "${progress:-}" "$level" "$slug" <<<"$python_code" >/dev/null 2>&1 || true
}

compute_progress_fraction() {
  python3 - "$progress_step" "$progress_total" <<'PY'
import sys

step = int(sys.argv[1])
total = max(int(sys.argv[2]), 1)
print(f"{min(max(step / total, 0.0), 1.0):.4f}")
PY
}

advance_progress() {
  progress_step=$((progress_step + 1))
  local message="${1:-Self-test progress}"
  local status="${2:-running}"
  local phase="${3:-selftest}"
  local slug="${4:-selftest_loop}"
  local fraction
  fraction="$(compute_progress_fraction)"
  emit_monitor_event "$phase" "stage" "$message" "$status" "$fraction" "" "$slug"
}

set_progress_plan() {
  progress_total="${1:-1}"
  progress_step=0
}

read_monitor_metadata() {
  if [[ ! -f "$monitor_port_file" ]]; then
    return 1
  fi
  local raw
  raw="$(python3 - "$monitor_port_file" <<'PY' 2>/dev/null || true
import json
import sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        info = json.load(fh)
except FileNotFoundError:
    raise SystemExit(1)

url = info.get("url")
port = info.get("port")
if isinstance(port, int):
    print(url or f"http://localhost:{port}")
    print(port)
elif isinstance(port, str) and port.isdigit():
    print(url or f"http://localhost:{port}")
    print(int(port))
else:
    raise SystemExit(1)
PY
)"
  if [[ -z "$raw" ]]; then
    return 1
  fi
  mapfile -t metadata <<<"$raw"
  monitor_url="${metadata[0]}"
  monitor_port="${metadata[1]}"
  [[ -n "$monitor_url" ]] && [[ -n "$monitor_port" ]]
}

ensure_monitor_deps() {
  if [[ -d "$repo_root/monitor/node_modules" ]]; then
    return
  fi
  (cd "$repo_root" && npm --prefix monitor install --no-fund --no-audit >/dev/null 2>&1)
}

start_monitor() {
  ensure_monitor_deps
  rm -f "$monitor_port_file"
  (
    cd "$repo_root" || exit 1
    REPO_ROOT="$workspace" \
    LOG_DIR="$monitor_log_dir" \
    MONITOR_PORT="${MONITOR_PORT:-4321}" \
    OPEN_BROWSER="false" \
    node monitor/agent/launch-monitor.js --background >/dev/null 2>&1
  )
}

wait_for_monitor() {
  local attempts=0
  local max_attempts=60
  while (( attempts < max_attempts )); do
    if read_monitor_metadata; then
      local health_url="${monitor_url%/}/api/health"
      if curl -sf "$health_url" >/dev/null 2>&1; then
        printf '[i] Monitor UI listening at %s (port %s)\n' "$monitor_url" "$monitor_port" | tee -a "$log_file"
        return 0
      fi
    fi
    sleep 1
    attempts=$((attempts + 1))
  done
  echo "[!] Monitor failed to report healthy state" | tee -a "$log_file"
  exit 1
}

declare -a SLUGS=("hello_greet" "hello_cli")
declare -A TITLES
declare -A SUMMARIES
declare -A ACCEPTANCE

TITLES["hello_greet"]="Print a default greeting"
SUMMARIES["hello_greet"]="Ensure \`python -m hello\` prints 'Hello World'."
ACCEPTANCE["hello_greet"]="python -m hello outputs Hello World once; generator must only add tests and append Links/Spec Trace"

TITLES["hello_cli"]="Configure greeting via CLI"
SUMMARIES["hello_cli"]="Support --message, --repeat, and --quiet flags for the hello app."
ACCEPTANCE["hello_cli"]="python -m hello --message 'Hi' --repeat 2 prints Hi twice; generator must only add tests and append Links/Spec Trace"

set_progress_plan $((2 + ${#SLUGS[@]} * 2 + 1))

if [[ -d "$workspace" ]]; then
  rm -rf "$workspace"
fi
mkdir -p "$workspace"
mkdir -p "$workspace/.codex_ci"
mkdir -p "$monitor_log_dir"
: >"$monitor_events_file"
: >"$log_file"
export ROOT="$workspace"
export LOG_DIR="$monitor_log_dir"
export REX_MONITOR_EVENTS_FILE="$monitor_events_file"

start_monitor
wait_for_monitor
emit_monitor_event "selftest" "run_started" "Self-test loop initialising sandbox workspace" "running" "0.0" "" "selftest_loop"

cd "$workspace"
shim_dir="$workspace/.shim"
mkdir -p "$shim_dir"
ln -sf "$(command -v python3)" "$shim_dir/python"
export PATH="$shim_dir:$PATH"
if [[ -d "$workspace/src/src" ]]; then
  export PYTHONPATH="$workspace/src/src:${PYTHONPATH:-}"
else
  export PYTHONPATH="$workspace/src:${PYTHONPATH:-}"
fi
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
  local timestamp
  timestamp="$(date -u +%Y%m%d%H%M%S)"
  local latest_audit="$audit_dir/audit_${timestamp}_selftest.md"
  : >"$latest_audit"
  local audit_label="${latest_audit#$repo_root/}"
  emit_monitor_event "selftest" "stage" "Updating audit snapshot ${audit_label}" "running" "0.95" "" "selftest_loop"

  local status_output=""
  if [[ -x "$workspace/rex-codex" ]]; then
    status_output="$(cd "$workspace" && ./rex-codex status 2>&1 || true)"
  fi

  local specs_output=""
  if [[ -d "$workspace/tests/feature_specs" ]]; then
    specs_output="$(cd "$workspace" && find tests/feature_specs -type f -print | sort || true)"
  fi

  local monitor_home=""
  local monitor_summary=""
  if [[ -n "$monitor_url" ]]; then
    monitor_home="$(curl -fsSL "$monitor_url" 2>&1 || true)"
    monitor_summary="$(curl -fsSL "${monitor_url%/}/api/summary" 2>&1 || true)"
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
    if [[ -n "$monitor_url" ]]; then
      printf '### Monitor curl (%s)\n\n```\n' "$monitor_url"
      if [[ -n "$monitor_home" ]]; then
        printf '%s\n' "$monitor_home"
      else
        printf '<no response>\n'
      fi
      printf '```\n'
      printf '### Monitor summary (%s/api/summary)\n\n```\n' "${monitor_url%/}"
      if [[ -n "$monitor_summary" ]]; then
        printf '%s\n' "$monitor_summary"
      else
        printf '<no response>\n'
      fi
      printf '```\n'
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

  if (( exit_status == 0 )); then
    emit_monitor_event "selftest" "completed" "Self-test loop completed (audit ${audit_label})" "succeeded" "1.0" "" "selftest_loop"
  else
    emit_monitor_event "selftest" "completed" "Self-test loop failed (exit ${exit_status})" "failed" "1.0" "error" "selftest_loop"
  fi

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
cat > README.md <<'MD'
# selftest workspace

This sandbox exercises `./rex-codex loop`, including `./rex-codex discriminator --feature-only`
and `./rex-codex discriminator --global`.
MD
mkdir -p src/hello
cat > src/hello/__init__.py <<'PY'
from __future__ import annotations

import argparse
from collections.abc import Sequence

DEFAULT_MESSAGE = "Hello World"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print a configurable greeting.")
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
run git add -A
run git commit -q -m "chore: seed runtime"
advance_progress "Seeded sandbox runtime" "succeeded" "selftest" "selftest_loop"

export REPO_URL="$repo_root"
export REX_AGENT_CHANNEL=main
export REX_AGENT_FORCE=1
export REX_AGENT_SKIP_DOCTOR=1

run bash "$repo_root/packaging/install.sh" --force --channel main
advance_progress "Installed rex-codex agent into sandbox" "succeeded" "selftest" "selftest_loop"

if [[ -z "${CODEX_BIN:-}" ]]; then
  export CODEX_BIN="npx --yes @openai/codex"
fi
export REX_AGENT_NO_UPDATE=1
export PYTHON=python3
export PYENV_VERSION="${PYENV_VERSION:-3.11.8}"
export GENERATOR_PROGRESS_SECONDS="${GENERATOR_PROGRESS_SECONDS:-5}"
export DISCRIMINATOR_PROGRESS_SECONDS="${DISCRIMINATOR_PROGRESS_SECONDS:-5}"

for slug in "${SLUGS[@]}"; do
  emit_monitor_event "generator" "run_started" "Generator starting for ${slug}" "running" "" "" "$slug"
  run ./rex-codex card new "$slug" \
    --title "${TITLES[$slug]}" \
    --summary "${SUMMARIES[$slug]}" \
    --acceptance "${ACCEPTANCE[$slug]}"

  run ./rex-codex generator "documents/feature_cards/${slug}.md" --single-pass
  advance_progress "Generator pass for ${slug}" "succeeded" "generator" "$slug"
  emit_monitor_event "discriminator" "run_started" "Feature discriminator starting for ${slug}" "running" "" "" "$slug"
  run ./rex-codex discriminator --feature-only --single-pass --disable-llm
  advance_progress "Feature discriminator for ${slug}" "succeeded" "discriminator" "$slug"
done

run ./rex-codex discriminator --global --single-pass --disable-llm
advance_progress "Global discriminator sweep" "succeeded" "discriminator" "global"
global_status=$last_status
