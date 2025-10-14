#!/usr/bin/env bash
# lib/loop.sh
set -Eeuo pipefail

rex_cmd_loop(){
  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  local MAX_PASSES="${MAX_PASSES:-100}"
  local CODEX_BIN="${CODEX_BIN:-npx --yes @openai/codex}"; : "${CODEX_FLAGS:=--yolo}"
  local MODEL="${MODEL:-}"
  local DISABLE_LLM="${DISABLE_LLM:-0}"

  echo "=== rex-codex loop (max passes: $MAX_PASSES) ==="
  ensure_python
  configure_pytest

  for pass in $(seq 1 "$MAX_PASSES"); do
    echo "=== PASS $pass ==="
    : > .codex_ci_latest.log

    if run_stages; then
      echo "✅ Green: compliance suite passed"
      exit 0
    fi

    # Mechanical fixes first
    auto_style_fixes || true
    if run_stages; then
      echo "✅ Green after mechanical style fixes"
      exit 0
    fi

    # Codex hand-off
    if [[ "$DISABLE_LLM" == "1" ]]; then
      echo "LLM disabled; stopping after mechanical fixes."
      exit 2
    fi
    run_llm_once "$CODEX_BIN" "$CODEX_FLAGS" "$MODEL" || true
    if git diff --quiet; then
      echo "No diff from LLM; aborting."
      exit 2
    fi
    git add -A && git commit -m "chore(rex-codex): autofix pass $pass"
  done
  echo "Hit MAX_PASSES without going green"; exit 1
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
  PYTEST_FLAGS=(-q)
  if python - <<'PY' >/dev/null 2>&1; then
import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("xdist") else 1)
PY
    PYTEST_FLAGS+=(-n 6 --dist loadscope)
  fi
}

run_stage(){
  local id="$1" q="$2" cmd="$3"
  printf "\n  Question %s: %s\n    Command: %s\n" "$id" "$q" "$cmd"
  if bash -lc "$cmd" | tee -a .codex_ci_latest.log; then return 0; else return 1; fi
}

run_stages(){
  local rc=0
  echo "------------------------------------------------------------"
  echo "Stage: Level 00 - Repo & System Health"
  run_stage "00.1" "Git status" "git status -sb" || rc=1
  run_stage "00.2" "Python version" "python3 --version" || rc=1
  [[ -x .venv/bin/python ]] && run_stage "00.3" "Venv Python" ".venv/bin/python --version" || rc=1

  echo "------------------------------------------------------------"
  echo "Stage: Level 01 - Tooling & Dependencies"
  run_stage "01.1" "pytest importable?" "python -c 'import pytest; print(pytest.__version__)'" || rc=1

  echo "------------------------------------------------------------"
  echo "Stage: Level 02 - Inline Spec Smoke"
  run_stage "02.1" "Do doctests/specs pass?" "pytest -q -k 'spec or doctest'"

  echo "------------------------------------------------------------"
  echo "Stage: Level 03 - Unit Test Grid"
  run_stage "03.1" "Run unit tests (no DB markers)" "pytest ${PYTEST_FLAGS[*]} -m 'not django_db'"

  echo "------------------------------------------------------------"
  echo "Stage: Level 06 - Style & Type Gates"
  local LINT_TARGETS="."
  run_stage "06.1" "black --check" "black $LINT_TARGETS --check" || rc=1
  run_stage "06.2" "isort --check-only" "isort $LINT_TARGETS --check-only" || rc=1
  run_stage "06.3" "ruff check" "ruff check $LINT_TARGETS" || rc=1
  run_stage "06.4" "flake8" "flake8 $LINT_TARGETS" || rc=1
  run_stage "06.5" "mypy" "mypy $LINT_TARGETS" || rc=1

  echo "  Result: [$([[ $rc == 0 ]] && echo PASS || echo FAIL)]"
  return "$rc"
}

auto_style_fixes(){
  echo "Mechanical fixes (ruff/black/isort)…"
  ruff check . --fix || true
  black . || true
  isort . || true
  if ! git diff --quiet; then git add -A && git commit -m "style(rex-codex): apply ruff/black/isort"; return 0; fi
  return 1
}

summarize_log(){
  tail -n 120 .codex_ci_latest.log 2>/dev/null || true
}

run_llm_once(){
  local bin="$1" flags="$2" model="$3"
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
    echo "--- BEGIN AGENTS.md EXCERPT ---"
    sed -n '1,300p' AGENTS.md || true
    echo "--- END AGENTS.md EXCERPT ---"
    echo
    echo "Latest log excerpt:"
    echo '```'
    summarize_log
    echo '```'
  } > "$prompt_file"
  local model_arg=""; [[ -n "$model" ]] && model_arg="--model $model"
  echo "[*] Invoking Codex with $bin $flags $model_arg"
  $bin exec $flags ${model_arg:+$model_arg} --cd "$PWD" -- "$(cat "$prompt_file")" || true
}
