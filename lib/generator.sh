#!/usr/bin/env bash
# lib/generator.sh
set -Eeuo pipefail
shopt -s lastpipe

rex_cmd_generator(){
  if type rex_self_update >/dev/null 2>&1; then
    rex_self_update || true
  fi
  local continuous=1
  local max_passes="${GENERATOR_MAX_PASSES:-5}"
  local focus_override=""
  local card_arg=""
  local iterate_all=0
  local statuses=("proposed")

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --continuous) continuous=1 ;;
      --single-pass) continuous=0 ;;
      --max-passes=*) max_passes="${1#*=}" ;;
      --max-passes) shift || true; max_passes="${1:-$max_passes}" ;;
      --focus=*) focus_override="${1#*=}" ;;
      --focus) shift || true; focus_override="${1:-}" ;;
      --include-accepted) statuses+=("accepted") ;;
      --status=*) generator_set_statuses "${1#*=}" statuses ;;
      --status)
        shift || { echo "[generator] --status requires a value" >&2; return 2; }
        generator_set_statuses "${1:-proposed}" statuses ;;
      --statuses=*) generator_set_statuses "${1#*=}" statuses ;;
      --statuses)
        shift || { echo "[generator] --statuses requires a value" >&2; return 2; }
        generator_set_statuses "${1:-proposed}" statuses ;;
      --each|--each-feature|--all) iterate_all=1 ;;
      --help) generator_usage; return 0 ;;
      --) shift || true; break ;;
      -* ) echo "[generator] Unknown option: $1" >&2; generator_usage >&2; return 2 ;;
      * ) card_arg="$1"; shift || true; break ;;
    esac
    shift || true
  done

  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  mkdir -p .codex_ci
  if command -v flock >/dev/null 2>&1; then
    exec 7>.codex_ci/rex_generator.lock
    if ! flock -n 7; then
      echo "[generator] Another generator process is running. Exiting."
      return 2
    fi
  fi
  local cards=()
  if [[ -n "$card_arg" ]]; then
    if [[ ! -f "$card_arg" ]]; then
      echo "[generator] Feature Card not found: $card_arg" >&2
      return 1
    fi
    cards=("$card_arg")
  else
    mapfile -t cards < <(generator_collect_cards statuses)
  fi

  if [[ ${#cards[@]} -eq 0 ]]; then
    local status_list="${statuses[*]}"
    echo "[generator] No Feature Cards with statuses: $status_list" >&2
    return 1
  fi

  local codex_bin="${CODEX_BIN:-npx --yes @openai/codex}"
  local codex_flags="${CODEX_FLAGS:---yolo}"
  local codex_model="${MODEL:-}"

  if [[ $iterate_all -eq 1 ]]; then
    local overall=0
    local card
    for card in "${cards[@]}"; do
      echo "[generator] === Processing card $card ==="
      if ! generator_process_card "$card" "$focus_override" "$continuous" "$max_passes" "$codex_bin" "$codex_flags" "$codex_model"; then
        overall=$?
        break
      fi
    done
    return $overall
  fi

  generator_process_card "${cards[0]}" "$focus_override" "$continuous" "$max_passes" "$codex_bin" "$codex_flags" "$codex_model"
}

generator_usage(){
  cat <<'USAGE'
Usage: rex-codex generator [options] [documents/feature_cards/<slug>.md]
Options:
  --continuous            Iterate generator + critic until DONE (default)
  --single-pass           Run a single generator invocation
  --max-passes <n>        Limit generator+critic iterations (default: $GENERATOR_MAX_PASSES or 5)
  --focus <notes>         Seed additional coverage notes for the first pass
  --include-accepted      Also consider Feature Cards marked status: accepted
  --status <name>         Only consider Feature Cards with the given status (comma separated allowed)
  --statuses <list>       Alias for --status
  --each, --each-feature  Process every matching Feature Card sequentially
USAGE
}

generator_set_statuses(){
  local raw="$1"
  local -n ref="$2"
  ref=()
  IFS=',' read -ra ref <<<"$raw"
  local idx
  for idx in "${!ref[@]}"; do
    ref[$idx]="${ref[$idx],,}"
  done
  if [[ ${#ref[@]} -eq 0 ]]; then
    ref=("proposed")
  fi
}

generator_collect_cards(){
  local -n statuses_ref="$1"
  local matches=()
  shopt -s nullglob
  for card in documents/feature_cards/*.md; do
    local status
    status="$(generator_card_status "$card")"
    for s in "${statuses_ref[@]}"; do
      if [[ "$status" == "$s" ]]; then
        matches+=("$card")
        break
      fi
    done
  done
  shopt -u nullglob
  printf '%s\n' "${matches[@]}"
}

generator_process_card(){
  local card="$1"
  local focus_override="$2"
  local continuous="$3"
  local max_passes="$4"
  local codex_bin="$5"
  local codex_flags="$6"
  local codex_model="$7"

  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  local slug="$(generator_slug_from_card "$card")"
  local status="$(generator_card_status "$card")"

  local focus="$focus_override"
  local pass

  if [[ "$continuous" -eq 0 ]]; then
    generator_run_once "$card" "$slug" "$status" "$focus" 1 "$codex_bin" "$codex_flags" "$codex_model"
    return $?
  fi

  for pass in $(seq 1 "$max_passes"); do
    echo "[generator] Iteration $pass/$max_passes (slug: $slug, status: $status)"
    if ! generator_run_once "$card" "$slug" "$status" "$focus" "$pass" "$codex_bin" "$codex_flags" "$codex_model"; then
      return $?
    fi
    generator_run_tests_log "$slug"
    local critic_feedback=""
    if generator_run_critic "$card" "$slug" critic_feedback "$pass" "$codex_bin" "$codex_flags" "$codex_model"; then
      echo "[generator] Critic returned DONE after pass $pass"
      return 0
    fi
    if [[ -z "$critic_feedback" ]]; then
      echo "[generator] Critic response empty; stopping." >&2
      return 5
    fi
    echo "[generator] Critic requested coverage updates:"
    echo "$critic_feedback"
    focus="$critic_feedback"
  done

  echo "[generator] Hit max passes ($max_passes) without critic approval." >&2
  return 6
}

generator_card_status(){
  local card="$1"
  local status
  status="$(awk '
    BEGIN{IGNORECASE=1}
    match($0, /^[[:space:]]*status:[[:space:]]*([[:alnum:]_.-]+)/, m){print tolower(m[1]); exit}
  ' "$card")"
  [[ -z "$status" ]] && status="unknown"
  echo "$status"
}

generator_card_is_proposed(){
  [[ "$(generator_card_status "$1")" == "proposed" ]]
}

generator_slug_from_card(){
  local card="$1"
  local base
  base="$(basename "$card")"
  base="${base%.md}"
  echo "$base"
}

generator_run_once(){
  local card="$1"
  local slug="$2"
  local status="$3"
  local focus="$4"
  local pass="$5"
  local codex_bin="$6"
  local codex_flags="$7"
  local codex_model="$8"

  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  local specs_dir="tests/feature_specs/$slug"
  mkdir -p "$specs_dir"

  local prompt=".codex_ci/generator_prompt.txt"
  local response=".codex_ci/generator_response.log"
  local patch=".codex_ci/generator_patch.diff"

  {
    cat <<'HDR'
You are a senior test architect.
Produce a *unified git diff* that adds deterministic pytest specs under tests/feature_specs/<feature>/.
Only touch:
- tests/feature_specs/<feature>/...
- documents/feature_cards/<same-card>.md  (to update state/links once tests are created)

Guardrails:
- Follow AGENTS.md. Do NOT modify runtime.
- Tests must import the intended module so first failure is ModuleNotFoundError.
- Force offline defaults (no network/time.sleep).
- Include happy-path, env toggle, and explicit error coverage.
Diff contract: unified diff only (start each file with 'diff --git').
Determinism:
- Avoid non-determinism (seed randomness, freeze time, avoid sleeps and network).
- Prefer explicit assertions and minimal fixtures; ensure failures point to the right module.
HDR
    echo
    echo "Feature slug: $slug"
    echo "All updates must remain under tests/feature_specs/$slug/ and the card document."
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
    generator_append_existing_tests "$slug"
  } > "$prompt"

  local -a BIN_ARR=()
  local -a FLAGS_ARR=()
  local -a CMD=()
  # shellcheck disable=SC2206
  BIN_ARR=($codex_bin)
  # shellcheck disable=SC2206
  FLAGS_ARR=($codex_flags)
  CMD=( "${BIN_ARR[@]}" exec )
  CMD+=( "${FLAGS_ARR[@]}" )
  if [[ -n "$codex_model" ]]; then
    CMD+=( --model "$codex_model" )
  fi
  CMD+=( --cd "$ROOT" -- "$(cat "$prompt")" )

  if ! "${CMD[@]}" > "$response" 2>&1; then
    cat "$response" >&2
    return 2
  fi

  if ! generator_extract_diff "$response" "$patch" "$slug"; then
    return 3
  fi

  if [[ ! -s "$patch" ]]; then
    echo "[generator] Codex response did not contain a usable diff"
    return 3
  fi

  if ! generator_enforce_patch_size "$patch"; then
    echo "[generator] Generated diff exceeds safety limits; rejecting."
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

  if ! generator_guard_card_edits "$slug"; then
    echo "[generator] Card edit policy violated; rejecting diff."
    generator_revert_generated_files "$slug"
    return 7
  fi

  if ! generator_enforce_hermetic_tests "$slug"; then
    echo "[generator] Non-hermetic patterns detected in generated specs; rejecting diff."
    generator_revert_generated_files "$slug"
    return 7
  fi

  if [[ "$status" == "proposed" ]]; then
    generator_update_metadata "$card" "$slug"
  fi

  echo "[generator] Specs updated from $card"
  return 0
}

generator_append_existing_tests(){
  local slug="$1"
  local first=1
  shopt -s nullglob
  for test_file in tests/feature_specs/"$slug"/*.py tests/feature_specs/"$slug"/**/*.py; do
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
  local slug="$3"
  python3 - "$response" "$patch_path" "$slug" <<'PY'
import re
import sys
from pathlib import Path

response, patch_path, slug = sys.argv[1:4]
text = Path(response).read_text(encoding="utf-8", errors="replace")
pattern = re.compile(r"^diff --git .*$", re.MULTILINE)
segments = []
allowed_doc = f"documents/feature_cards/{slug}.md"
allowed_prefix = f"tests/feature_specs/{slug}/"
for match in pattern.finditer(text):
    start = match.start()
    next_match = pattern.search(text, match.end())
    block = text[start : next_match.start()] if next_match else text[start:]
    header = block.splitlines()[0]
    m = re.match(r"^diff --git a/(.*?) b/(.*?)$", header)
    if not m:
        continue
    a_path, b_path = m.group(1), m.group(2)
    for candidate in (a_path, b_path):
        if candidate.startswith(allowed_prefix) or candidate == allowed_doc:
            segments.append(block.strip())
            break
Path(patch_path).write_text("\n\n".join(segments), encoding="utf-8")
PY
}

generator_enforce_hermetic_tests(){
  local slug="$1"
  local specs_dir="tests/feature_specs/$slug"
  if [[ ! -d "$specs_dir" ]]; then
    return 0
  fi
  python3 - "$specs_dir" <<'PY'
import ast
import sys
from pathlib import Path

specs_dir = Path(sys.argv[1])
if not specs_dir.exists():
    sys.exit(0)

banned_import_modules = {
    "requests": "network access via requests",
    "httpx": "network access via httpx",
    "urllib": "network access via urllib",
    "urllib3": "network access via urllib3",
    "aiohttp": "network access via aiohttp",
    "socket": "network access via socket",
    "subprocess": "subprocess usage requires explicit stubbing",
    "secrets": "secrets module is non-deterministic; inject fixed values instead",
    "numpy": "numpy.random requires fixed seeding; avoid in specs",
}
banned_call_prefixes = {
    "requests.": "network access via requests",
    "httpx.": "network access via httpx",
    "urllib.": "network access via urllib",
    "urllib3.": "network access via urllib3",
    "aiohttp.": "network access via aiohttp",
    "socket.": "network access via socket",
    "secrets.": "secrets module is non-deterministic; inject fixed values instead",
    "numpy.random.": "numpy.random must be seeded deterministically; avoid direct usage",
    "random.SystemRandom.": "SystemRandom uses system entropy; avoid in specs",
}
banned_call_exact = {
    "time.sleep": "time.sleep introduces nondeterministic delays",
    "asyncio.sleep": "asyncio.sleep introduces nondeterministic delays",
    "subprocess.run": "subprocess usage requires explicit stubbing",
    "subprocess.Popen": "subprocess usage requires explicit stubbing",
    "subprocess.call": "subprocess usage requires explicit stubbing",
    "os.system": "os.system usage should be avoided in specs",
    "time.time": "use a deterministic clock stub instead of time.time",
    "time.perf_counter": "use a deterministic clock stub instead of time.perf_counter",
    "time.monotonic": "use a deterministic clock stub instead of time.monotonic",
    "datetime.datetime.now": "use a frozen datetime or dependency injection",
    "datetime.datetime.utcnow": "use a frozen datetime or dependency injection",
    "datetime.datetime.today": "use a frozen datetime or dependency injection",
    "datetime.date.today": "use a frozen date or dependency injection",
    "pytest.skip": "skipping generated specs is not allowed",
    "pytest.xfail": "xfailing generated specs is not allowed",
    "os.urandom": "use deterministic stubs instead of os.urandom",
    "uuid.uuid4": "use a fixed UUID in specs instead of uuid.uuid4",
    "uuid.uuid1": "use a fixed UUID in specs instead of uuid.uuid1",
}
random_prefixes = ("random.", "numpy.random.")
random_allowed = {"random.seed", "numpy.random.seed"}

class HermeticVisitor(ast.NodeVisitor):
    def __init__(self, path):
        self.path = path
        self.aliases = {}
        self.violations = []

    def add_violation(self, lineno, detail):
        self.violations.append((self.path, lineno, detail))

    def visit_Import(self, node):
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[-1]
            self.aliases[name] = alias.name
            root = alias.name.split(".")[0]
            if root in banned_import_modules:
                self.add_violation(node.lineno, f"import {alias.name} ({banned_import_modules[root]})")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        module = node.module or ""
        root = module.split(".")[0] if module else ""
        if root in banned_import_modules:
            self.add_violation(node.lineno, f"from {module} import ... ({banned_import_modules[root]})")
        for alias in node.names:
            target = f"{module}.{alias.name}" if module else alias.name
            name = alias.asname or alias.name
            self.aliases[name] = target
        self.generic_visit(node)

    def resolve(self, node):
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value)
            if base:
                return f"{base}.{node.attr}"
            return node.attr
        return None

    def visit_Call(self, node):
        call_name = self.resolve(node.func)
        if call_name:
            if call_name in banned_call_exact:
                self.add_violation(node.lineno, f"{call_name} ({banned_call_exact[call_name]})")
            elif any(call_name.startswith(prefix) for prefix in random_prefixes) and call_name not in random_allowed:
                self.add_violation(node.lineno, f"{call_name} (set a deterministic seed or avoid randomness)")
            else:
                for prefix, reason in banned_call_prefixes.items():
                    if call_name.startswith(prefix):
                        self.add_violation(node.lineno, f"{call_name} ({reason})")
                        break
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        for dec in node.decorator_list:
            name = self.resolve(dec)
            if not name:
                continue
            lname = name.lower()
            if lname.startswith("pytest.mark.skip") or lname.startswith("pytest.mark.xfail"):
                self.add_violation(getattr(dec, "lineno", node.lineno), f"{name} (skipping/xfailing specs is forbidden)")
            elif lname.startswith("pytest.mark.skipif"):
                args = getattr(dec, "args", [])
                if args:
                    first = args[0]
                    value = getattr(first, "value", None)
                    if value is True:
                        self.add_violation(getattr(dec, "lineno", node.lineno), "pytest.mark.skipif(True, ...) is forbidden")
        self.generic_visit(node)

violations = []
for path in specs_dir.rglob("*.py"):
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        violations.append((path, 0, "file is not UTF-8 decodable"))
        continue
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        violations.append((path, exc.lineno or 0, f"syntax error: {exc}"))
        continue
    visitor = HermeticVisitor(path)
    visitor.visit(tree)
    violations.extend(visitor.violations)

if violations:
    for path, lineno, detail in violations:
        location = f"{path}:{lineno}" if lineno else str(path)
        print(f"{location}: {detail}")
    sys.exit(1)

sys.exit(0)
PY
  return $?
}

generator_revert_generated_files(){
  local slug="$1"
  local specs_dir="tests/feature_specs/$slug"
  if [[ -d "$specs_dir" ]]; then
    while IFS= read -r tracked; do
      [[ -z "$tracked" ]] && continue
      git restore --worktree -- "$tracked" || true
    done < <(git ls-files "$specs_dir")
    git clean -fd -- "$specs_dir" >/dev/null 2>&1 || true
  fi
  local card="documents/feature_cards/${slug}.md"
  if git ls-files --error-unmatch "$card" >/dev/null 2>&1; then
    git restore --staged --worktree -- "$card" >/dev/null 2>&1 || true
  elif [[ -f "$card" ]]; then
    rm -f -- "$card"
  fi
}

generator_guard_card_edits(){
  local slug="$1"
  local card="documents/feature_cards/${slug}.md"
  [[ -f "$card" ]] || return 0
  local diff_cached
  diff_cached="$(git diff --cached --unified=0 -- "$card")"
  if [[ -z "$diff_cached" ]]; then
    diff_cached="$(git diff --unified=0 -- "$card")"
  fi
  [[ -z "$diff_cached" ]] && return 0
  if printf '%s\n' "$diff_cached" | python3 - "$card" <<'PY'
import difflib
import subprocess
import sys
from pathlib import Path
import re

card_path = Path(sys.argv[1])
diff_text = sys.stdin.read()
if not diff_text.strip():
    sys.exit(0)

if re.search(r'^[+-]\s*status\s*:', diff_text, flags=re.IGNORECASE | re.MULTILINE):
    print("[generator] Card edit touches status line; abort.")
    sys.exit(1)

try:
    before = subprocess.check_output(
        ["git", "show", f"HEAD:{card_path.as_posix()}"],
        stderr=subprocess.DEVNULL,
    ).decode("utf-8")
except subprocess.CalledProcessError:
    before = ""

after = card_path.read_text(encoding="utf-8")

before_lines = before.splitlines()
after_lines = after.splitlines()

allowed_headers = {"## Links", "## Spec Trace"}

def nearest_header(idx):
    for pos in range(idx - 1, -1, -1):
        stripped = after_lines[pos].strip()
        if stripped.startswith("## "):
            return stripped
    return None

sm = difflib.SequenceMatcher(a=before_lines, b=after_lines)
for tag, i1, i2, j1, j2 in sm.get_opcodes():
    if tag in ("delete", "replace"):
        print("[generator] Card edits may only append new lines inside allowed sections.")
        sys.exit(1)
    if tag == "insert":
        added = after_lines[j1:j2]
        for line in added:
            if re.search(r'\bstatus\s*:', line, flags=re.IGNORECASE):
                print("[generator] Card edit attempts to add a status line; abort.")
                sys.exit(1)
        header = nearest_header(j1)
        if header is None and added:
            candidate = added[0].strip()
            if candidate.startswith("## "):
                header = candidate
        if header is None:
            print("[generator] Card edits must appear under an allowed section.")
            sys.exit(1)
        header_normalized = header.strip()
        header_key = next((h for h in allowed_headers if header_normalized.lower().startswith(h.lower())), None)
        if header_key is None:
            print(f"[generator] Card edits under section '{header_normalized}' are not permitted.")
            sys.exit(1)

sys.exit(0)
PY
  then
    return 0
  else
    return 1
  fi
}

generator_enforce_patch_size(){
  local patch_path="$1"
  local max_files="${GENERATOR_MAX_FILES:-6}"
  local max_lines="${GENERATOR_MAX_LINES:-300}"
  python3 - "$patch_path" "$max_files" "$max_lines" <<'PY'
import sys
from pathlib import Path

patch_path, max_files, max_lines = sys.argv[1:]
max_files = int(max_files)
max_lines = int(max_lines)
files = 0
lines = 0
for line in Path(patch_path).read_text(encoding="utf-8", errors="replace").splitlines():
    if line.startswith("diff --git "):
        files += 1
    elif line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
        lines += 1
if files > max_files or lines > max_lines:
    print(f"[generator] diff touches {files} files / {lines} lines (limits {max_files}/{max_lines})")
    sys.exit(1)
sys.exit(0)
PY
}

generator_ensure_pytest(){
  command -v python3 >/dev/null || return 0
  [[ -d .venv ]] || python3 -m venv .venv
  (
    . .venv/bin/activate
    python - <<'PY' >/dev/null 2>&1 || python -m pip install -U pip pytest >/dev/null
import importlib
import sys
importlib.import_module("pytest")
PY
  )
}

generator_run_tests_log(){
  local slug="$1"
  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  local log=".codex_ci/generator_tests.log"
  local specs_dir="tests/feature_specs/$slug"
  if [[ ! -d "$specs_dir" ]]; then
    echo "[generator] No tests/feature_specs/$slug directory yet; skipping pytest snapshot."
    : > "$log"
    return 0
  fi
  generator_ensure_pytest || true
  if [[ -f .venv/bin/activate ]]; then
    . .venv/bin/activate
  fi
  export PYTHONHASHSEED="${PYTHONHASHSEED:-0}"
  local snapshot_timeout="${GENERATOR_SNAPSHOT_TIMEOUT:-300}"
  if command -v timeout >/dev/null 2>&1; then
    if timeout --preserve-status "$snapshot_timeout" pytest "$specs_dir" -q -x --maxfail=1 > "$log" 2>&1; then
      :
    else
      local status=$?
      if [[ $status -eq 124 ]]; then
        echo "[generator] Pytest snapshot timed out after ${snapshot_timeout}s" >> "$log"
      fi
    fi
  else
    pytest "$specs_dir" -q -x --maxfail=1 > "$log" 2>&1 || true
  fi
}

generator_run_critic(){
  local card="$1"
  local slug="$2"
  local __result_var="$3"
  local pass="$4"
  local codex_bin="$5"
  local codex_flags="$6"
  local codex_model="$7"

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
    echo "Feature slug: $slug"
    echo
    echo "--- FEATURE CARD ---"
    cat "$card"
    echo
    echo "--- CURRENT TEST FILES ---"
    shopt -s nullglob
    for test_file in tests/feature_specs/"$slug"/*.py tests/feature_specs/"$slug"/**/*.py; do
      echo "### $test_file"
      sed -n '1,300p' "$test_file"
      echo
    done
    shopt -u nullglob
    echo "--- END TEST FILES ---"
    echo
    if [[ -f "$tests_log" ]]; then
      echo "--- PYTEST OUTPUT (tests/feature_specs/$slug) ---"
      sed -n '1,200p' "$tests_log"
      echo
    fi
    if [[ -f .codex_ci_latest.log ]]; then
      echo "--- MOST RECENT DISCRIMINATOR LOG (tail) ---"
      tail -n 120 .codex_ci_latest.log
      echo
    fi
  } > "$prompt"

  local -a BIN_ARR=()
  local -a FLAGS_ARR=()
  local -a CMD=()
  # shellcheck disable=SC2206
  BIN_ARR=($codex_bin)
  # shellcheck disable=SC2206
  FLAGS_ARR=($codex_flags)
  CMD=( "${BIN_ARR[@]}" exec )
  CMD+=( "${FLAGS_ARR[@]}" )
  if [[ -n "$codex_model" ]]; then
    CMD+=( --model "$codex_model" )
  fi
  CMD+=( --cd "$ROOT" -- "$(cat "$prompt")" )

  if ! "${CMD[@]}" > "$response" 2>&1; then
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

  local normalized
  normalized="$(echo "$trimmed" | tr -d '`' | tr '[:space:]' ' ' | sed 's/^ *//;s/ *$//' | tr '[:lower:]' '[:upper:]')"
  if [[ "$normalized" == "DONE" ]]; then
    printf -v "$__result_var" ""
    return 0
  fi

  printf -v "$__result_var" "%s" "$trimmed"
  return 1
}

generator_update_metadata(){
  local card="$1"
  local slug="$2"
  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  python3 - "$ROOT/rex-agent.json" "$card" "$slug" <<'PY'
import json, sys, time
path, card, slug = sys.argv[1:4]
try:
    with open(path) as fh:
        data = json.load(fh)
except FileNotFoundError:
    data = {}
feature = data.setdefault("feature", {})
feature["active_card"] = card
feature["active_slug"] = slug
feature["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
with open(path, "w") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
}
