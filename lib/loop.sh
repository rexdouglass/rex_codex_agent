#!/usr/bin/env bash
# lib/loop.sh
set -Eeuo pipefail

source "$REX_SRC/lib/generator.sh"
source "$REX_SRC/lib/discriminator.sh"

rex_cmd_loop(){
  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  local run_generator=1
  local run_discriminator=1
  local generator_args=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --skip-generator)
        run_generator=0
        ;;
      --generator-only)
        run_discriminator=0
        ;;
      --discriminator-only)
        run_generator=0
        run_discriminator=1
        ;;
      --)
        shift
        while [[ $# -gt 0 ]]; do
          generator_args+=("$1")
          shift || true
        done
        break
        ;;
      *)
        generator_args+=("$1")
        ;;
    esac
    shift || true
  done

  local gen_status=1
  if [[ "$run_generator" -eq 1 ]]; then
    echo "=== rex-codex loop: generator phase ==="
    if rex_cmd_generator "${generator_args[@]}"; then
      gen_status=0
      echo "[loop] Generator produced new specs; running discriminator…"
    else
      gen_status=$?
      if [[ $gen_status -eq 1 ]]; then
        echo "[loop] Generator found no proposed Feature Cards; running discriminator anyway."
      else
        echo "[loop] Generator failed (exit $gen_status); aborting."
        return $gen_status
      fi
    fi
  else
    echo "[loop] Generator skipped; running discriminator only."
  fi

  if [[ "$run_discriminator" -eq 1 ]]; then
    echo "=== rex-codex loop: discriminator phase ==="
    rex_cmd_discriminator
  else
    echo "[loop] Discriminator skipped; generator phase complete."
  fi
}
