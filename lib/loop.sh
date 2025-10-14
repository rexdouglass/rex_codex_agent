#!/usr/bin/env bash
# lib/loop.sh
set -Eeuo pipefail

source "$REX_SRC/lib/generator.sh"
source "$REX_SRC/lib/discriminator.sh"

rex_cmd_loop(){
  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  local run_generator=1
  local run_discriminator=1
  local run_feature=1
  local run_global=1
  local each_features=0
  local generator_args=()
  local generator_statuses=("proposed")

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --skip-generator)
        run_generator=0
        ;;
      --generator-only)
        run_discriminator=0
        ;;
      --skip-feature)
        run_feature=0
        ;;
      --skip-global)
        run_global=0
        ;;
      --feature-only)
        run_feature=1
        run_global=0
        ;;
      --global-only)
        run_feature=0
        run_global=1
        ;;
      --include-accepted)
        generator_statuses+=("accepted")
        generator_args+=("$1")
        ;;
      --status=*)
        generator_set_statuses "${1#*=}" generator_statuses
        generator_args+=("$1")
        ;;
      --status)
        shift || { echo "[loop] --status requires a value" >&2; return 2; }
        generator_set_statuses "$1" generator_statuses
        generator_args+=("--status=$1")
        ;;
      --statuses=*)
        generator_set_statuses "${1#*=}" generator_statuses
        generator_args+=("$1")
        ;;
      --statuses)
        shift || { echo "[loop] --statuses requires a value" >&2; return 2; }
        generator_set_statuses "$1" generator_statuses
        generator_args+=("--statuses=$1")
        ;;
      --each-feature|--each)
        each_features=1
        generator_args+=("--each")
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

  local -a generator_filtered_args=()
  for arg in "${generator_args[@]}"; do
    [[ "$arg" == "--each" || "$arg" == "--each-feature" ]] && continue
    generator_filtered_args+=("$arg")
  done

  if [[ $each_features -eq 1 ]]; then
    local -a cards
    mapfile -t cards < <(generator_collect_cards generator_statuses)
    if [[ ${#cards[@]} -eq 0 ]]; then
      local statuses_join
      statuses_join="${generator_statuses[*]}"
      echo "[loop] No Feature Cards with statuses: $statuses_join"
      return 1
    fi
    local card slug
    for card in "${cards[@]}"; do
      slug="$(generator_slug_from_card "$card")"
      echo "=== rex-codex loop: processing $card (slug: $slug) ==="
      if [[ "$run_generator" -eq 1 ]]; then
        echo "=== rex-codex loop: generator phase ==="
        if ! rex_cmd_generator "$card" "${generator_filtered_args[@]}"; then
          local status=$?
          if [[ $status -eq 1 ]]; then
            echo "[loop] Generator skipped $card"
          else
            echo "[loop] Generator failed on $card (exit $status)"
            return $status
          fi
        fi
      else
        echo "[loop] Generator skipped for $card."
      fi

      if [[ "$run_discriminator" -eq 1 ]]; then
        loop_run_discriminator "$slug" "$run_feature" "$run_global" || return $?
      fi
    done
    return 0
  fi

  local gen_status=1
  if [[ "$run_generator" -eq 1 ]]; then
    echo "=== rex-codex loop: generator phase ==="
    if rex_cmd_generator "${generator_args[@]}"; then
      gen_status=0
      echo "[loop] Generator produced new specs; running discriminator…"
    else
      gen_status=$?
      if [[ $gen_status -eq 1 ]]; then
        echo "[loop] Generator found no matching Feature Cards; running discriminator anyway."
      else
        echo "[loop] Generator failed (exit $gen_status); aborting."
        return $gen_status
      fi
    fi
  else
    echo "[loop] Generator skipped; running discriminator only."
  fi

  if [[ "$run_discriminator" -eq 1 ]]; then
    local slug="$(rex_current_feature_slug)"
    echo "=== rex-codex loop: discriminator phase ==="
    loop_run_discriminator "$slug" "$run_feature" "$run_global"
  else
    echo "[loop] Discriminator skipped; generator phase complete."
  fi
}

loop_run_discriminator(){
  local slug="$1"
  local do_feature="$2"
  local do_global="$3"

  local run_slug=""
  if [[ -n "$slug" ]]; then
    run_slug="$slug"
  fi

  if [[ "$do_feature" -eq 1 ]]; then
    if [[ -n "$run_slug" ]]; then
      if ! rex_cmd_discriminator --feature-only --feature "$run_slug"; then
        return $?
      fi
    else
      echo "[loop] No active feature slug; skipping feature-only discriminator run."
    fi
  fi

  if [[ "$do_global" -eq 1 ]]; then
    rex_cmd_discriminator --global
  else
    echo "[loop] Global discriminator run skipped by flag."
  fi
}
