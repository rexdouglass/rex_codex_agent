#!/usr/bin/env bash
# lib/supervisor.sh
set -Eeuo pipefail

rex_cmd_supervise(){
  source "$REX_SRC/lib/loop.sh"
  rex_cmd_loop "$@"
}
