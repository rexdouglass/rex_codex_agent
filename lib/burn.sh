#!/usr/bin/env bash
# lib/burn.sh
set -Eeuo pipefail

rex_cmd_burn(){
  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  local FORCE=0 KEEP_AGENT=1

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --yes|-y) FORCE=1 ;;
      --purge-agent) KEEP_AGENT=0 ;;
      *) echo "Unknown option: $1" >&2; return 2 ;;
    esac
    shift
  done

  echo "WARNING: This will delete repository files in $ROOT."
  if [[ "$KEEP_AGENT" -eq 1 ]]; then
    echo "  - .rex_agent will be preserved"
  else
    echo "  - .rex_agent will also be removed"
  fi
  echo "  - .git directory is always preserved"

  if [[ "$FORCE" -ne 1 ]]; then
    echo -n "Type 'burn it down' to continue: "
    read -r confirmation
    [[ "$confirmation" == "burn it down" ]] || { echo "Aborted."; return 3; }
  fi

  shopt -s dotglob
  for entry in "$ROOT"/* "$ROOT"/.*; do
    [[ "$entry" == "$ROOT" ]] && continue
    case "$(basename "$entry")" in
      .|..) continue ;;
      .git) continue ;;
      .rex_agent)
        [[ "$KEEP_AGENT" -eq 1 ]] && continue
        ;;
    esac
    rm -rf "$entry"
  done
  shopt -u dotglob

  mkdir -p "$ROOT"
  echo "[✓] Repository reset. Re-run ./rex-codex init to seed fresh scaffolding."
}
