#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${1:-}" ]]; then
  echo "Usage: $0 <slug>" >&2
  exit 1
fi

slug="$1"
root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
events_file="$root/.codex_ci/events.jsonl"
diff_file="$root/.codex_ci/generator_patch.diff"
build_entry="$root/tui/dist/index.js"

quote() {
  local value="${1//\'/\'\\\'\'}"
  printf "'%s'" "$value"
}

install_cmd="if [ ! -d tui/node_modules ]; then npm --prefix tui install --no-fund --no-audit >/dev/null 2>&1 || exit 1; fi"
build_cmd="if [ ! -f tui/dist/index.js ]; then npm --prefix tui run build >/dev/null 2>&1 || exit 1; fi"
env_prefix="FORCE_COLOR=1 TUI_SLUG=$(quote "$slug") TUI_REPO_ROOT=$(quote "$root") TUI_EVENTS_FILE=$(quote "$events_file") TUI_DIFF_FILE=$(quote "$diff_file")"
command="cd $(quote "$root") && $install_cmd && $build_cmd && $env_prefix node $(quote "$build_entry")"

exec gnome-terminal \
  --title "rex-codex HUD :: $slug" \
  -- bash -lc "$command"
