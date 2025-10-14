#!/usr/bin/env bash
# lib/discriminator.sh
set -Eeuo pipefail
shopt -s lastpipe

DISCRIMINATOR_PROTECTED_PATHS_DEFAULT="tests documents pytest.ini pyproject.toml mypy.ini .flake8 .ruff.toml ruff.toml conftest.py tox.ini setup.cfg .coveragerc .pre-commit-config.yaml requirements.txt requirements-dev.txt requirements/*.txt constraints.txt constraints-*.txt Pipfile Pipfile.lock poetry.lock Dockerfile Dockerfile.* .github .gitlab-ci.yml Makefile noxfile.py"

rex_cmd_discriminator(){
  if type rex_self_update >/dev/null 2>&1; then
    rex_self_update || true
  fi
  local mode="global"
  local continuous=1
  local max_passes="${DISCRIMINATOR_MAX_PASSES:-25}"
  local slug=""
  local disable_llm="${DISABLE_LLM:-1}"
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
  mkdir -p .codex_ci
  if command -v flock >/dev/null 2>&1; then
    exec 8>.codex_ci/rex_discriminator.lock
    if ! flock -n 8; then
      echo "[discriminator] Another discriminator process is running. Exiting."
      return 2
    fi
  fi
  export PYTHONHASHSEED="${PYTHONHASHSEED:-0}"
  if [[ -d src && -z "${COVERAGE_TARGETS:-}" ]]; then
    export COVERAGE_TARGETS=src
  fi
  : "${COVERAGE_MIN:=80}"
  export DISCRIMINATOR_LOG=".codex_ci/latest_discriminator.log"
  [[ -z "$slug" ]] && slug="$(rex_current_feature_slug)"
  if [[ "$mode" == "feature" && -z "$slug" ]]; then
    echo "[discriminator] No active feature slug; falling back to global sweep"
    mode="global"
  fi

  ensure_python
  configure_pytest "$mode"
  DISCRIMINATOR_STAGE_TIMEOUT="${DISCRIMINATOR_STAGE_TIMEOUT:-900}"
  if command -v timeout >/dev/null 2>&1; then
    DISCRIMINATOR_HAS_TIMEOUT=1
  else
    DISCRIMINATOR_HAS_TIMEOUT=0
    echo "[discriminator] Warning: 'timeout' utility not found; stage timeouts disabled."
  fi
  if [[ "$disable_llm" != "1" ]] && ! command -v node >/dev/null 2>&1; then
    echo "[discriminator] Node.js not found; forcing DISABLE_LLM=1."
    disable_llm=1
  fi
  if [[ "$disable_llm" == "1" ]]; then
    echo "[discriminator] LLM integration disabled (set DISABLE_LLM=0 to enable)."
  fi

  local passes=0
  while (( passes < max_passes )); do
    passes=$((passes + 1))
    echo "=== rex-codex discriminator ($mode) pass $passes/$max_passes ==="
    : > "$DISCRIMINATOR_LOG"
    : > .codex_ci_latest.log

    if discriminator_run_stages "$mode" "$slug"; then
      echo "✅ Green: $mode suite passed"
      discriminator_record_success "$mode" "$slug" ""
      return 0
    fi

    if [[ "$continuous" -eq 0 ]]; then
      echo "[discriminator] Stopping after first failing pass (--single-pass)."
      return 1
    fi

    discriminator_auto_style "$mode" "$slug" || true
    if discriminator_run_stages "$mode" "$slug"; then
      echo "✅ Green after mechanical fixes"
      discriminator_record_success "$mode" "$slug" ""
      return 0
    fi

    if [[ "$disable_llm" == "1" ]]; then
      echo "LLM disabled; stopping after mechanical fixes."
      return 2
    fi
    local tests_before=""
    tests_before="$(discriminator_collect_test_count "$mode" "$slug")"
    local protected_snapshot_file
    protected_snapshot_file="$(mktemp -t rex_discriminator_protected.XXXXXX)"
    discriminator_snapshot_protected_files > "$protected_snapshot_file" || true
    run_llm_once "$CODEX_BIN_LOCAL" "$CODEX_FLAGS_LOCAL" "$MODEL_LOCAL" "$mode" "$slug" || true
    local protected_changes=""
    if ! protected_changes="$(discriminator_detect_protected_changes "$protected_snapshot_file")"; then
      echo "[discriminator] Aborting pass; LLM patch touched protected paths."
      discriminator_revert_paths "$protected_changes"
      rm -f "$protected_snapshot_file"
      return 2
    fi
    if ! discriminator_reject_non_runtime_changes; then
      echo "[discriminator] Aborting pass; LLM patch touched non-runtime paths."
      discriminator_revert_all_changes
      rm -f "$protected_snapshot_file"
      return 2
    fi
    rm -f "$protected_snapshot_file"
    if git diff --quiet; then
      echo "No diff from LLM; aborting."
      return 2
    fi
    local tests_after=""
    tests_after="$(discriminator_collect_test_count "$mode" "$slug")"
    if [[ -n "$tests_before" && -n "$tests_after" ]]; then
      if (( tests_after < tests_before )); then
        echo "[discriminator] Test collection decreased ($tests_before -> $tests_after); rejecting LLM patch."
        discriminator_revert_all_changes
        return 2
      fi
    fi
    if ! discriminator_enforce_patch_size; then
      echo "[discriminator] Aborting pass; LLM patch exceeded size limits."
      return 2
    fi
    git add -A && git commit -m "chore(rex-codex): discriminator ${mode} pass $passes"
    discriminator_record_success "$mode" "$slug" "$tests_after"
  done

  echo "Hit max passes ($max_passes) without going green"
  return 1
}

discriminator_usage(){
  cat <<'USAGE'
Usage: rex-codex discriminator [options]
  --feature-only         Run only the active feature shard (defaults to latest generator card)
  --global               Run the full ladder (default)
  --continuous           Keep iterating until green (default; override via DISCRIMINATOR_MAX_PASSES)
  --single-pass          Run one pass and stop (even if failing)
  --max-passes <n>       Maximum passes before giving up (default: 25)
  --feature <slug>       Override feature slug for feature-only mode
USAGE
}

ensure_python(){
  command -v python3 >/dev/null || { echo "python3 missing"; exit 3; }
  [[ -d .venv ]] || python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip >/dev/null
  local requirements_template="$REX_SRC/templates/requirements-dev.txt"
  if ! python - <<'PY' >/dev/null 2>&1; then
import importlib.util
import sys

REQUIRED = ("pytest", "pytest_cov", "black", "isort", "ruff", "flake8", "mypy")
missing = [name for name in REQUIRED if importlib.util.find_spec(name) is None]
if missing:
    sys.exit(1)
sys.exit(0)
PY
    if [[ -f "$requirements_template" ]]; then
      python -m pip install -r "$requirements_template" >/dev/null
    else
      python -m pip install \
        pytest==8.0.2 \
        pytest-xdist==3.5.0 \
        pytest-cov==4.1.0 \
        black==24.4.2 \
        isort==5.13.2 \
        ruff==0.3.2 \
        flake8==7.0.0 \
        mypy==1.8.0 >/dev/null
    fi
  fi
}

configure_pytest(){
  local mode="$1"
  PYTEST_FLAGS=(-q -ra)
  if [[ "$mode" == "feature" ]]; then
    PYTEST_FLAGS+=(-x --maxfail=1)
    return
  fi
  if python -c "import importlib.util as util, sys; sys.exit(0 if util.find_spec('xdist') else 1)" >/dev/null 2>&1; then
    PYTEST_FLAGS+=(-n auto --dist loadscope)
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
    run_stage "02.1" "Run feature specs" "pytest ${PYTEST_FLAGS[*]} $specs_dir --junitxml .codex_ci/discriminator_feature_${slug}.xml" || rc=1

    echo "------------------------------------------------------------"
    echo "Stage: Level 03 - Feature Unit Grid ($slug)"
    run_stage "03.1" "Run feature specs (no DB markers)" "pytest ${PYTEST_FLAGS[*]} $specs_dir -m 'not django_db'" || rc=1

    if [[ -n "${COVERAGE_MIN:-}" ]]; then
      echo "------------------------------------------------------------"
      echo "Stage: Level 04 - Feature Coverage ($slug)"
      local coverage_targets="${COVERAGE_TARGETS:-.}"
      run_stage "04.1" "Coverage threshold" "pytest ${PYTEST_FLAGS[*]} $specs_dir --cov=$coverage_targets --cov-report=term --cov-fail-under=$COVERAGE_MIN" || rc=1
    fi

    if [[ "${PIP_AUDIT:-0}" == "1" ]]; then
      run_stage "05.1" "pip-audit (dependencies)" "python -m pip install -q pip-audit >/dev/null 2>&1 && pip-audit" || rc=1
    fi
    if [[ "${BANDIT:-0}" == "1" ]]; then
      local bandit_targets="${BANDIT_TARGETS:-${COVERAGE_TARGETS:-src}}"
      [[ -d "$bandit_targets" ]] || bandit_targets="."
      run_stage "05.2" "bandit (static security)" "python -m pip install -q bandit >/dev/null 2>&1 && bandit -q -r $bandit_targets" || rc=1
    fi
    if [[ "${PACKAGE_CHECK:-0}" == "1" ]]; then
      run_stage "05.3" "Build distribution artifacts" "python -m pip install -q build twine >/dev/null 2>&1 && python -m build" || rc=1
      run_stage "05.4" "twine check dist/*" "python -m pip install -q build twine >/dev/null 2>&1 && twine check dist/*" || rc=1
    fi

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
    run_stage "02.1" "Do doctests/specs pass?" "pytest ${PYTEST_FLAGS[*]} -k 'spec or doctest' --junitxml .codex_ci/discriminator_global_smoke.xml" || rc=1

    echo "------------------------------------------------------------"
    echo "Stage: Level 03 - Unit Test Grid"
    run_stage "03.1" "Run unit tests (no DB markers)" "pytest ${PYTEST_FLAGS[*]} -m 'not django_db' --junitxml .codex_ci/discriminator_global_unit.xml" || rc=1

    if [[ -n "${COVERAGE_MIN:-}" ]]; then
      echo "------------------------------------------------------------"
      echo "Stage: Level 04 - Coverage"
      local coverage_targets="${COVERAGE_TARGETS:-.}"
      run_stage "04.1" "Coverage threshold" "pytest ${PYTEST_FLAGS[*]} --cov=$coverage_targets --cov-report=term --cov-fail-under=$COVERAGE_MIN" || rc=1
    fi

    if [[ "${PIP_AUDIT:-0}" == "1" ]]; then
      run_stage "05.1" "pip-audit (dependencies)" "python -m pip install -q pip-audit >/dev/null 2>&1 && pip-audit" || rc=1
    fi
    if [[ "${BANDIT:-0}" == "1" ]]; then
      local bandit_targets="${BANDIT_TARGETS:-${COVERAGE_TARGETS:-src}}"
      [[ -d "$bandit_targets" ]] || bandit_targets="."
      run_stage "05.2" "bandit (static security)" "python -m pip install -q bandit >/dev/null 2>&1 && bandit -q -r $bandit_targets" || rc=1
    fi
    if [[ "${PACKAGE_CHECK:-0}" == "1" ]]; then
      run_stage "05.3" "Build distribution artifacts" "python -m pip install -q build twine >/dev/null 2>&1 && python -m build" || rc=1
      run_stage "05.4" "twine check dist/*" "python -m pip install -q build twine >/dev/null 2>&1 && twine check dist/*" || rc=1
    fi

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
    local -a style_targets=()
    if [[ -d src ]]; then
      style_targets+=("src")
    fi
    shopt -s nullglob
    for pkg_init in */__init__.py; do
      [[ -e "$pkg_init" ]] || continue
      local pkg_dir="${pkg_init%/__init__.py}"
      case "$pkg_dir" in
        tests|test|documents|docs) continue ;;
      esac
      style_targets+=("$pkg_dir")
    done
    shopt -u nullglob
    if [[ ${#style_targets[@]} -eq 0 ]]; then
      echo "[discriminator] No runtime targets detected for mechanical style; skipping."
      return 1
    fi
    declare -A seen_targets=()
    local -a unique_targets=()
    local candidate
    for candidate in "${style_targets[@]}"; do
      [[ -z "$candidate" ]] && continue
      if [[ -z "${seen_targets[$candidate]:-}" ]]; then
        seen_targets[$candidate]=1
        unique_targets+=("$candidate")
      fi
    done
    if [[ ${#unique_targets[@]} -eq 0 ]]; then
      echo "[discriminator] No runtime targets detected for mechanical style; skipping."
      return 1
    fi
    ruff check "${unique_targets[@]}" --fix || true
    black "${unique_targets[@]}" || true
    isort "${unique_targets[@]}" || true
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
  local stage_timeout="${DISCRIMINATOR_STAGE_TIMEOUT:-0}"
  local has_timeout="${DISCRIMINATOR_HAS_TIMEOUT:-1}"
  local -a stage_cmd=(bash -lc "$cmd")
  local use_timeout=0
  if [[ "$stage_timeout" =~ ^[0-9]+$ && "$stage_timeout" -gt 0 ]]; then
    if [[ "$has_timeout" -eq 1 ]]; then
      use_timeout=1
      stage_cmd=(timeout --preserve-status "$stage_timeout" "${stage_cmd[@]}")
    fi
  fi
  if "${stage_cmd[@]}" | tee -a "$DISCRIMINATOR_LOG" | tee -a .codex_ci_latest.log; then
    return 0
  else
    local stage_status="${PIPESTATUS[0]:-1}"
    if [[ "$use_timeout" -eq 1 && "$stage_status" -eq 124 ]]; then
      local msg="[discriminator] Stage $id timed out after ${stage_timeout}s"
      echo "$msg"
      [[ -n "$DISCRIMINATOR_LOG" ]] && echo "$msg" >> "$DISCRIMINATOR_LOG"
      echo "$msg" >> .codex_ci_latest.log
    fi
    return 1
  fi
}

summarize_log(){
  if [[ -n "$DISCRIMINATOR_LOG" && -f "$DISCRIMINATOR_LOG" ]]; then
    tail -n 120 "$DISCRIMINATOR_LOG" 2>/dev/null || true
  else
    tail -n 120 .codex_ci_latest.log 2>/dev/null || true
  fi
}

run_llm_once(){
  local bin="$1" flags="$2" model="$3" mode="$4" slug="$5"
  mkdir -p .codex_ci
  local prompt_file=".codex_ci/discriminator_prompt.txt"
  local runtime_allowlist
  runtime_allowlist="$(discriminator_detect_runtime_targets || true)"
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
    echo "Runtime directories permitted for edits:"
    if [[ -n "$runtime_allowlist" ]]; then
      while IFS= read -r target; do
        [[ -z "$target" ]] && continue
        printf ' - %s\n' "$target"
      done <<<"$runtime_allowlist"
    else
      echo " - (none discovered; edits outside protected files likely to be rejected)"
    fi
    echo
    cat <<'EOH'
Latest log excerpt:
```
EOH
    summarize_log
    echo '```'
  } > "$prompt_file"
  local -a BIN_ARR=()
  local -a FLAGS_ARR=()
  local -a CMD=()
  # shellcheck disable=SC2206
  BIN_ARR=($bin)
  # shellcheck disable=SC2206
  FLAGS_ARR=($flags)
  CMD=( "${BIN_ARR[@]}" exec )
  CMD+=( "${FLAGS_ARR[@]}" )
  if [[ -n "$model" ]]; then
    CMD+=( --model "$model" )
  fi
  CMD+=( --cd "$PWD" -- "$(cat "$prompt_file")" )
  echo "[*] Invoking Codex with: ${CMD[*]}"
  local llm_log=".codex_ci/discriminator_llm_response.log"
  {
    echo "=== $(date -u +'%Y-%m-%dT%H:%M:%SZ') ==="
    "${CMD[@]}"
  } | tee -a "$llm_log" || true
}

discriminator_record_success(){
  local mode="$1"
  local slug="$2"
  local test_hint="$3"
  local ROOT
  ROOT="$(rex_repo_root)"
  cd "$ROOT"
  local test_count="${test_hint:-}"
  if [[ -z "$test_count" ]]; then
    test_count="$(discriminator_collect_test_count "$mode" "$slug")"
  fi
  python3 - "$ROOT/rex-agent.json" "$mode" "$slug" "$test_count" <<'PY' 2>/dev/null
import json, sys, time
path, mode, slug, test_count = sys.argv[1:5]
try:
    data = json.load(open(path))
except Exception:
    data = {}
disc = data.setdefault("discriminator", {})
disc["last_mode"] = mode
disc["last_slug"] = slug or None
disc["last_green_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
try:
    tc = int(test_count)
    disc["last_test_count"] = tc
except Exception:
    pass
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
}

discriminator_collect_test_count(){
  local mode="$1" slug="$2"
  local -a cmd=(pytest --collect-only)
  if [[ "$mode" == "feature" && -n "$slug" ]]; then
    local specs_dir="tests/feature_specs/$slug"
    if [[ -d "$specs_dir" ]]; then
      cmd+=("$specs_dir")
    fi
  fi
  local output
  if ! output="$("${cmd[@]}" 2>/dev/null)"; then
    echo ""
    return 0
  fi
  python3 <<'PY' <<<"$output"
import re
import sys

text = sys.stdin.read()
match = re.search(r"collected (\d+) items?", text, re.IGNORECASE)
print(match.group(1) if match else "")
PY
}

discriminator_detect_runtime_targets(){
  local overrides="${DISCRIMINATOR_RUNTIME_ALLOWLIST:-}"
  if [[ -n "$overrides" ]]; then
    read -r -a runtime <<<"$overrides"
    printf '%s\n' "${runtime[@]}" | sed '/^$/d' | sort -u
    return 0
  fi
  local -a detected=()
  [[ -d src ]] && detected+=("src")
  shopt -s nullglob
  for pkg_init in */__init__.py; do
    [[ -e "$pkg_init" ]] || continue
    local pkg_dir="${pkg_init%/__init__.py}"
    case "$pkg_dir" in
      tests|test|documents|docs|.git|.github) continue ;;
    esac
    detected+=("$pkg_dir")
  done
  shopt -u nullglob
  printf '%s\n' "${detected[@]}" | sed '/^$/d' | sort -u
}

discriminator_reject_non_runtime_changes(){
  local -a runtime_targets
  mapfile -t runtime_targets < <(discriminator_detect_runtime_targets)
  if [[ ${#runtime_targets[@]} -eq 0 ]]; then
    return 0
  fi
  local -a rejects=()
  local changed
  while IFS= read -r changed; do
    [[ -z "$changed" ]] && continue
    [[ "$changed" == ".codex_ci/"* ]] && continue
    local allowed=0 target
    for target in "${runtime_targets[@]}"; do
      [[ -z "$target" ]] && continue
      if [[ "$changed" == "$target" || "$changed" == "$target/"* ]]; then
        allowed=1
        break
      fi
    done
    if [[ $allowed -eq 0 ]]; then
      rejects+=("$changed")
    fi
  done < <((git diff --name-only; git ls-files --others --exclude-standard) | sort -u)

  if [[ ${#rejects[@]} -gt 0 ]]; then
    echo "[discriminator] LLM edits outside runtime allowlist: ${rejects[*]}"
    discriminator_revert_paths "$(printf '%s\n' "${rejects[@]}")"
    return 1
  fi
  return 0
}

discriminator_enforce_patch_size(){
  local max_files="${DISCRIMINATOR_MAX_FILES:-6}"
  local max_lines="${DISCRIMINATOR_MAX_LINES:-300}"
  local files=0 lines=0 added deleted
  while read -r added deleted _path; do
    [[ -z "$added" || -z "$deleted" ]] && continue
    if [[ "$added" == "-" || "$deleted" == "-" ]]; then
      ((files++))
      ((lines+=max_lines+1))
      continue
    fi
    ((files++))
    ((lines+=added+deleted))
  done < <(git diff --numstat)
  if (( files > max_files || lines > max_lines )); then
    echo "[discriminator] LLM patch touched $files files / $lines lines (limits ${max_files}/${max_lines})."
    discriminator_revert_all_changes
    return 1
  fi
  return 0
}

discriminator_revert_all_changes(){
  git restore --staged --worktree --source=HEAD :/ >/dev/null 2>&1 || git reset --hard -q
}

discriminator_snapshot_protected_files(){
  local patterns="${DISCRIMINATOR_PROTECTED_PATHS:-$DISCRIMINATOR_PROTECTED_PATHS_DEFAULT}"
  local -a pattern_arr=()
  read -r -a pattern_arr <<<"$patterns"
  python3 - "${pattern_arr[@]}" <<'PY'
import glob
import hashlib
import sys
from pathlib import Path

patterns = sys.argv[1:] or []
paths = set()

def record_path(target: Path):
    if not target.exists():
        return
    if target.is_dir():
        for path in target.rglob("*"):
            if path.is_file():
                paths.add(path)
    elif target.is_file():
        paths.add(target)

for pattern in patterns:
    if not pattern:
        continue
    matches = glob.glob(pattern, recursive=True)
    if not matches and not any(ch in pattern for ch in "*?[]"):
        candidate = Path(pattern)
        if candidate.exists():
            matches = [candidate.as_posix()]
    for match in matches:
        candidate = Path(match)
        record_path(candidate)

for path in sorted(paths, key=lambda p: p.as_posix()):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"{path.as_posix()}|{digest}")
PY
}

discriminator_detect_protected_changes(){
  local baseline_file="$1"
  local current_file
  current_file="$(mktemp -t rex_discriminator_protected_after.XXXXXX)"
  discriminator_snapshot_protected_files > "$current_file" || true
  local result
  result="$(
    python3 - "$baseline_file" "$current_file" <<'PY'
import sys

def read_snapshot(path):
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                name, digest = line.split("|", 1)
                data[name] = digest
    except FileNotFoundError:
        pass
    return data

before = read_snapshot(sys.argv[1])
after = read_snapshot(sys.argv[2])
changed = set()

for path, digest in before.items():
    if path not in after:
        changed.add(path)
    elif after[path] != digest:
        changed.add(path)

for path in after:
    if path not in before:
        changed.add(path)

if changed:
    for path in sorted(changed):
        print(path)
    sys.exit(1)
sys.exit(0)
PY
  )"
  local status=$?
  rm -f "$current_file"
  if (( status != 0 )); then
    printf '%s\n' "$result"
    return 1
  fi
  return 0
}

discriminator_revert_paths(){
  local changed_paths="$1"
  local path
  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    if git ls-files --error-unmatch "$path" >/dev/null 2>&1; then
      git restore --staged -- "$path" >/dev/null 2>&1 || true
      git restore --worktree -- "$path" || true
    elif [[ -e "$path" ]]; then
      rm -rf -- "$path"
    fi
  done <<<"$changed_paths"
}
