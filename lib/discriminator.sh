#!/usr/bin/env bash
# lib/discriminator.sh
set -Eeuo pipefail

rex_cmd_discriminator(){
  local mode="global"
  local continuous=1
  local max_passes="${DISCRIMINATOR_MAX_PASSES:-25}"
  local slug=""
  local disable_llm="${DISABLE_LLM:-0}"
  local CODEX_BIN_LOCAL="${CODEX_BIN:-npx --yes @openai/codex}"
  local CODEX_FLAGS_LOCAL="${CODEX_FLAGS:---yolo}"
  local MODEL_LOCAL="${MODEL:-}"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --feature-only)
        mode="feature"
        ;;
      --global)
        mode="global"
        ;;
      --continuous)
        continuous=1
        ;;
      --single-pass)
        continuous=0
        ;;
      --max-passes=*)
        max_passes="${1#*=}"
        ;;
      --max-passes)
        shift || true
        max_passes="${1:-$max_passes}"
        ;;
      --feature=*)
        slug="${1#*=}"
        ;;
      --feature)
        shift || true
        slug="${1:-}"
        ;;
      --help)
        discriminator_usage
        return 0
        ;;
      --)
        shift || true
        break
        ;;
      *)
        echo "[discriminator] Unknown option: $1" >&2
        discriminator_usage >&2
        return 2
        ;;
    esac
    shift || true
  done

  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  [[ -z "$slug" ]] && slug="$(rex_current_feature_slug)"
  if [[ "$mode" == "feature" && -z "$slug" ]]; then
    echo "[discriminator] No active feature slug; falling back to global sweep"
    mode="global"
  fi

  ensure_python
  configure_pytest "$mode"

  local passes=0
  while (( passes < max_passes )); do
    passes=$((passes + 1))
    echo "=== rex-codex discriminator ($mode) pass $passes/$max_passes ==="
    : > .codex_ci_latest.log

    if discriminator_run_stages "$mode" "$slug"; then
      echo "✅ Green: $mode suite passed"
      return 0
    fi

    if [[ "$continuous" -eq 0 ]]; then
      echo "[discriminator] Stopping after first failing pass (--single-pass)."
      return 1
    fi

    discriminator_auto_style "$mode" "$slug" || true
    if discriminator_run_stages "$mode" "$slug"; then
      echo "✅ Green after mechanical fixes"
      return 0
    fi

    if [[ "$disable_llm" == "1" ]]; then
      echo "LLM disabled; stopping after mechanical fixes."
      return 2
    fi
    run_llm_once "$CODEX_BIN_LOCAL" "$CODEX_FLAGS_LOCAL" "$MODEL_LOCAL" "$mode" "$slug" || true
    if git diff --quiet; then
      echo "No diff from LLM; aborting."
      return 2
    fi
    git add -A && git commit -m "chore(rex-codex): discriminator ${mode} pass $passes"
  done

  echo "Hit max passes ($max_passes) without going green"
  return 1
}

discriminator_usage(){
  cat <<'USAGE'
Usage: rex-codex discriminator [options]
  --feature-only         Run only the active feature shard (defaults to latest generator card)
  --global               Run the full ladder (default)
  --continuous           Keep iterating until green (default)
  --single-pass          Run one pass and stop (even if failing)
  --max-passes <n>       Maximum passes before giving up (default: 25)
  --feature <slug>       Override feature slug for feature-only mode
USAGE
}

ensure_python(){
  command -v python3 >/dev/null || { echo "python3 missing"; exit 3; }
  [[ -d .venv ]] || python3 -m venv .venv
  . .venv/bin/activate
  python - <<'PY' >/dev/null 2>&1 || python -m pip install -U pip pytest pytest-xdist black isort ruff flake8 mypy >/dev/null
import importlib,sys
for m in ("pytest","black","isort","ruff","flake8","mypy"):
    importlib.import_module(m)
PY
}

configure_pytest(){
  local mode="$1"
  PYTEST_FLAGS=(-q)
  if [[ "$mode" == "feature" ]]; then
    return
  fi
  if python -c "import importlib.util as util, sys; sys.exit(0 if util.find_spec('xdist') else 1)" >/dev/null 2>&1; then
    PYTEST_FLAGS+=(-n 6 --dist loadscope)
  fi
}

discriminator_run_stages(){
  local mode="$1"
  local slug="$2"
  local rc=0
  local specs_dir="tests/feature_specs/$slug"
  local doc_path="documents/feature_cards/${slug}.md"

  echo "------------------------------------------------------------"
  echo "Stage: Level 00 - Repo & System Health"
  run_stage "00.1" "Git status" "git status -sb" || rc=1
  run_stage "00.2" "Python version" "python3 --version" || rc=1
  [[ -x .venv/bin/python ]] && run_stage "00.3" "Venv Python" ".venv/bin/python --version" || rc=1

  echo "------------------------------------------------------------"
  echo "Stage: Level 01 - Tooling & Dependencies"
  run_stage "01.1" "pytest importable?" "python -c 'import pytest; print(pytest.__version__)'" || rc=1

  if [[ "$mode" == "feature" ]]; then
    if [[ ! -d "$specs_dir" ]]; then
      echo "[discriminator] Feature specs directory $specs_dir missing" | tee -a .codex_ci_latest.log
      return 1
    fi
    echo "------------------------------------------------------------"
    echo "Stage: Level 02 - Feature Spec Smoke ($slug)"
    run_stage "02.1" "Run feature specs" "pytest -q $specs_dir" || rc=1

    echo "------------------------------------------------------------"
    echo "Stage: Level 03 - Feature Unit Grid ($slug)"
    run_stage "03.1" "Run feature specs (no DB markers)" "pytest -q $specs_dir -m 'not django_db'" || rc=1

    echo "------------------------------------------------------------"
    echo "Stage: Level 06 - Feature Style & Type Gates ($slug)"
    local style_targets=("$specs_dir")
    run_stage "06.1" "black --check (feature)" "black ${style_targets[*]} --check" || rc=1
    run_stage "06.2" "isort --check-only (feature)" "isort ${style_targets[*]} --check-only" || rc=1
    run_stage "06.3" "ruff check (feature)" "ruff check ${style_targets[*]}" || rc=1
    run_stage "06.4" "flake8 (feature)" "flake8 ${style_targets[*]}" || rc=1
    run_stage "06.5" "mypy (feature)" "mypy ${style_targets[*]}" || rc=1
  else
    echo "------------------------------------------------------------"
    echo "Stage: Level 02 - Inline Spec Smoke"
    run_stage "02.1" "Do doctests/specs pass?" "pytest -q -k 'spec or doctest'" || rc=1

    echo "------------------------------------------------------------"
    echo "Stage: Level 03 - Unit Test Grid"
    run_stage "03.1" "Run unit tests (no DB markers)" "pytest ${PYTEST_FLAGS[*]} -m 'not django_db'" || rc=1

    echo "------------------------------------------------------------"
    echo "Stage: Level 06 - Style & Type Gates"
    local LINT_TARGETS="."
    run_stage "06.1" "black --check" "black $LINT_TARGETS --check" || rc=1
    run_stage "06.2" "isort --check-only" "isort $LINT_TARGETS --check-only" || rc=1
    run_stage "06.3" "ruff check" "ruff check $LINT_TARGETS" || rc=1
    run_stage "06.4" "flake8" "flake8 $LINT_TARGETS" || rc=1
    run_stage "06.5" "mypy" "mypy $LINT_TARGETS" || rc=1
  fi

  echo "  Result: [$([[ $rc == 0 ]] && echo PASS || echo FAIL)]"
  return "$rc"
}

discriminator_auto_style(){
  local mode="$1"
  local slug="$2"
  local specs_dir="tests/feature_specs/$slug"

  echo "Mechanical fixes (ruff/black/isort)…"
  if [[ "$mode" == "feature" ]]; then
    if [[ -d "$specs_dir" ]]; then
      ruff check "$specs_dir" --fix || true
      black "$specs_dir" || true
      isort "$specs_dir" || true
    fi
  else
    ruff check . --fix || true
    black . || true
    isort . || true
  fi
  if ! git diff --quiet; then
    git add -A && git commit -m "style(rex-codex): apply ruff/black/isort ($mode)"
    return 0
  fi
  return 1
}

run_stage(){
  local id="$1" q="$2" cmd="$3"
  printf "\n  Question %s: %s\n    Command: %s\n" "$id" "$q" "$cmd"
  if bash -lc "$cmd" | tee -a .codex_ci_latest.log; then return 0; else return 1; fi
}

summarize_log(){
  tail -n 120 .codex_ci_latest.log 2>/dev/null || true
}

run_llm_once(){
  local bin="$1" flags="$2" model="$3" mode="$4" slug="$5"
  local prompt_file=".codex_prompt.txt"
  {
    cat <<'EOH'
You are a coding agent for this repository.
Follow AGENTS.md guardrails (runtime vs tests, doc/spec/type, offline by default).
Make ONE minimal change that most reduces non-compliance or failures.
Do not weaken tests or remove functionality.
After edits, run relevant commands locally to validate.
EOH
    echo
    echo "Current discriminator mode: $mode"
    if [[ -n "$slug" ]]; then
      echo "Active feature slug: $slug"
    fi
    echo
    echo "--- BEGIN AGENTS.md EXCERPT ---"
    sed -n '1,300p' AGENTS.md || true
    echo "--- END AGENTS.md EXCERPT ---"
    echo
    echo "Latest log excerpt:"
    echo '```'
    summarize_log
    echo '```'
  } > "$prompt_file"
  local model_arg=""
  [[ -n "$model" ]] && model_arg="--model $model"
  echo "[*] Invoking Codex with $bin $flags $model_arg"
  $bin exec $flags ${model_arg:+$model_arg} --cd "$PWD" -- "$(cat "$prompt_file")" || true
}
