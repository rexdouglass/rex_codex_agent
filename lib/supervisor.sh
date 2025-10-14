#!/usr/bin/env bash
# lib/supervisor.sh
set -Eeuo pipefail

rex_cmd_supervise(){
  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  local CARD="${1:-}"
  echo "[*] Supervisor: ensuring feature specs exist before running loop"
  source "$REX_SRC/lib/feature_creator.sh"
  rex_cmd_feature "$CARD" || echo "[!] Feature creator exited non-zero; continuing to loop"
  source "$REX_SRC/lib/loop.sh"
  rex_cmd_loop
}
