#!/usr/bin/env bash
# lib/generator.sh
set -Eeuo pipefail

rex_cmd_generator(){
  local continuous=1
  local max_passes="${GENERATOR_MAX_PASSES:-5}"
  local card_arg=""
  local focus_override=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --continuous) continuous=1 ;;
      --single-pass) continuous=0 ;;
      --max-passes=*) max_passes="${1#*=}" ;;
      --max-passes)
        shift || true
        max_passes="${1:-$max_passes}"
        ;;
      --focus=*)
        focus_override="${1#*=}"
        ;;
      --help)
        generator_usage
        return 0
        ;;
      --) shift; break ;;
      -*)
        echo "[generator] Unknown option: $1" >&2
        generator_usage >&2
        return 2
        ;;
      *)
        card_arg="$1"
        ;;
    esac
    shift || true
    if [[ -n "$card_arg" ]]; then
      break
    fi
  done

  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"

  GENERATOR_CODEX_BIN="${CODEX_BIN:-npx --yes @openai/codex}"
  GENERATOR_CODEX_FLAGS="${CODEX_FLAGS:---yolo}"
  GENERATOR_CODEX_MODEL="${MODEL:-}"

  local card_path
  if ! card_path="$(generator_select_card "$card_arg")"; then
    echo "$card_path"
    return 1
  fi

  mkdir -p .codex_ci
  local focus="$focus_override"

  if [[ "$continuous" -eq 0 ]]; then
    generator_run_once "$card_path" "$focus" 1
    return $?
  fi

  for pass in $(seq 1 "$max_passes"); do
    echo "[generator] Iteration $pass/$max_passes"
    if ! generator_run_once "$card_path" "$focus" "$pass"; then
      return $?
    fi
    generator_run_tests_log
    local critic_feedback=""
    if generator_run_critic "$card_path" critic_feedback "$pass"; then
      echo "[generator] Critic returned DONE after pass $pass"
      return 0
    fi
    if [[ -z "$critic_feedback" ]]; then
      echo "[generator] Critic response empty; stopping." >&2
      return 5
    fi
    echo "[generator] Critic requested further coverage:"
    echo "$critic_feedback"
    focus="$critic_feedback"
  done

  echo "[generator] Hit max passes ($max_passes) without critic approval." >&2
  return 6
}

generator_usage(){
  cat <<'USAGE'
Usage: rex-codex generator [options] [documents/feature_cards/<slug>.md]
Options:
  --continuous          Iterate generator + critic until DONE (default)
  --single-pass         Run a single generator invocation
  --max-passes <n>      Limit number of generator+critic iterations (default: $GENERATOR_MAX_PASSES or 5)
  --focus <notes>       Seed additional coverage notes for the first pass
USAGE
}

generator_select_card(){
  local requested="${1:-}"
  local candidates=()
  if [[ -n "$requested" ]]; then
    if [[ ! -f "$requested" ]]; then
      printf '[generator] Feature Card not found: %s\n' "$requested"
      return 1
    fi
    if ! generator_card_is_proposed "$requested"; then
      printf '[generator] Card %s is not marked with "status: proposed"\n' "$requested"
      return 1
    fi
    printf '%s' "$requested"
    return 0
  fi

  shopt -s nullglob
  for path in documents/feature_cards/*.md; do
    generator_card_is_proposed "$path" && candidates+=("$path")
  done
  shopt -u nullglob

  if [[ "${#candidates[@]}" -eq 0 ]]; then
    echo "[generator] No Feature Cards with status: proposed"
    return 1
  fi

  printf '%s' "${candidates[0]}"
}

generator_card_is_proposed(){
  local card="$1"
  grep -Eq '^status:\s*proposed\s*$' "$card"
}

generator_run_once(){
  local card="$1"
  local focus="${2:-}"
  local pass="${3:-1}"
  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"

  local prompt=".codex_ci/generator_prompt.txt"
  local response=".codex_ci/generator_response.log"
  local patch=".codex_ci/generator_patch.diff"

  {
    cat <<'HDR'
You are a senior test architect.
Produce a *unified git diff* that adds deterministic pytest specs under tests/.
Only touch:
- tests/feature_specs/...
- documents/feature_cards/<same-card>.md  (to update state/links once tests are created)

Guardrails:
- Follow AGENTS.md. Do NOT modify runtime.
- Tests must import the intended module so first failure is ModuleNotFoundError.
- Force offline defaults (no network/time.sleep).
- Include happy-path, env toggle, and explicit error coverage.
Diff contract: unified diff only (start each file with 'diff --git').
HDR
    echo
    echo "--- PASS NUMBER ---"
    echo "$pass"
    echo
    if [[ -n "$focus" ]]; then
      echo "Additional coverage goals from previous critic pass:"
      echo "$focus"
      echo
    fi
    echo "--- BEGIN AGENTS.md EXCERPT ---"
    sed -n '1,300p' AGENTS.md || true
    echo "--- END AGENTS.md EXCERPT ---"
    echo
    echo "--- BEGIN FEATURE CARD ---"
    cat "$card"
    echo
    echo "--- END FEATURE CARD ---"
    generator_append_existing_tests
  } > "$prompt"

  local model_arg=()
  [[ -n "$GENERATOR_CODEX_MODEL" ]] && model_arg=(--model "$GENERATOR_CODEX_MODEL")

  if ! "$GENERATOR_CODEX_BIN" exec $GENERATOR_CODEX_FLAGS "${model_arg[@]}" --cd "$ROOT" -- "$(cat "$prompt")" > "$response" 2>&1; then
    cat "$response" >&2
    return 2
  fi

  if ! generator_extract_diff "$response" "$patch"; then
    return 3
  fi

  if [[ ! -s "$patch" ]]; then
    echo "[generator] Codex response did not contain a usable diff"
    return 3
  fi

  if ! git apply --index "$patch"; then
    echo "[generator] git apply --index failed; retrying without --index"
    if ! git apply "$patch"; then
      echo "[generator] Failed to apply Codex diff"
      return 4
    fi
    git add tests documents/feature_cards || true
  fi

  echo "[generator] Specs updated from $card"
  return 0
}

generator_append_existing_tests(){
  local tests_glob=tests/feature_specs/*.py
  local first=1
  shopt -s nullglob
  for test_file in $tests_glob; do
    if [[ "$first" -eq 1 ]]; then
      echo
      echo "--- EXISTING TEST FILES ---"
      first=0
    fi
    echo
    echo "### $test_file"
    sed -n '1,300p' "$test_file"
  done
  shopt -u nullglob
}

generator_extract_diff(){
  local response="$1"
  local patch_path="$2"
  python3 - "$response" "$patch_path" <<'PY'
import re
import sys
from pathlib import Path

response, patch_path = map(Path, sys.argv[1:3])
text = response.read_text(encoding="utf-8", errors="replace")
pattern = re.compile(r"^diff --git .*$", re.MULTILINE)
segments = []
for match in pattern.finditer(text):
    start = match.start()
    next_match = pattern.search(text, match.end())
    block = text[start : next_match.start()] if next_match else text[start:]
    header = block.splitlines()[0]
    parts = header.split()
    target = parts[2][2:] if len(parts) >= 3 else ""
    if target.startswith("tests/feature_specs/") or target.startswith("documents/feature_cards/"):
        segments.append(block.strip())

Path(patch_path).write_text("\n\n".join(segments), encoding="utf-8")
PY
}

generator_run_tests_log(){
  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  local log=".codex_ci/generator_tests.log"
  if [[ ! -d tests/feature_specs ]]; then
    echo "[generator] No tests/feature_specs directory yet; skipping pytest snapshot."
    : > "$log"
    return 0
  fi
  if [[ -x .venv/bin/activate ]]; then
    # shellcheck source=/dev/null
    . .venv/bin/activate
  fi
  pytest tests/feature_specs -q > "$log" 2>&1 || true
}

generator_run_critic(){
  local card="$1"
  local __result_var="$2"
  local pass="${3:-1}"
  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"

  local prompt=".codex_ci/generator_critic_prompt.txt"
  local response=".codex_ci/generator_critic_response.log"
  local tests_log=".codex_ci/generator_tests.log"

  {
    cat <<'HDR'
You are reviewing pytest specs that were just generated for the following Feature Card.
Decide whether the tests fully capture the acceptance criteria and obvious negative cases.
Respond in ONE of two ways:
1. `DONE` (exact uppercase word) if coverage is sufficient.
2. `TODO:` followed by bullet items describing additional scenarios to cover.
Do NOT provide code; only guidance.
HDR
    echo
    echo "--- GENERATOR PASS ---"
    echo "$pass"
    echo
    echo "--- FEATURE CARD ---"
    cat "$card"
    echo
    echo "--- CURRENT TEST FILES ---"
    shopt -s nullglob
    for test_file in tests/feature_specs/*.py; do
      echo "### $test_file"
      sed -n '1,300p' "$test_file"
      echo
    done
    shopt -u nullglob
    echo "--- END TEST FILES ---"
    echo
    if [[ -f "$tests_log" ]]; then
      echo "--- PYTEST OUTPUT (tests/feature_specs) ---"
      sed -n '1,200p' "$tests_log"
      echo
    fi
  } > "$prompt"

  local model_arg=()
  [[ -n "$GENERATOR_CODEX_MODEL" ]] && model_arg=(--model "$GENERATOR_CODEX_MODEL")

  if ! "$GENERATOR_CODEX_BIN" exec $GENERATOR_CODEX_FLAGS "${model_arg[@]}" --cd "$ROOT" -- "$(cat "$prompt")" > "$response" 2>&1; then
    cat "$response" >&2
    printf -v "$__result_var" ""
    return 2
  fi

  local trimmed
  trimmed="$(python3 - "$response" <<'PY'
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
print(text.strip())
PY
  )"

  if [[ -z "$trimmed" ]]; then
    printf -v "$__result_var" ""
    return 1
  fi

  if [[ "$trimmed" =~ ^DONE$ ]]; then
    printf -v "$__result_var" ""
    return 0
  fi

  if [[ "$trimmed" =~ ^TODO: ]]; then
    printf -v "$__result_var" "%s" "$trimmed"
    return 1
  fi

  printf -v "$__result_var" "%s" "$trimmed"
  return 1
}
