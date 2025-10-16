#!/usr/bin/env bash
set -euo pipefail
if [[ -z "${1:-}" ]]; then
  echo "Usage: $0 <slug>" >&2
  exit 1
fi
slug="$1"
args=("./bin/rex-codex" "hud" "generator" "--slug" "$slug" "--follow")
exec gnome-terminal --title "rex-codex HUD :: $slug" -- bash -lc "cd /media/skynet3/8tb_a1/rex_codex_agent && ${args[*]}"
