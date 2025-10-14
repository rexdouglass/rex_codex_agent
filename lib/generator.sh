#!/usr/bin/env bash
# lib/generator.sh
set -Eeuo pipefail

rex_cmd_generator(){
  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  local CODEX_BIN="${CODEX_BIN:-npx --yes @openai/codex}"; : "${CODEX_FLAGS:=--yolo}"
  local MODEL="${MODEL:-}"
  mkdir -p .codex_ci
  local PROMPT=".codex_ci/generator_prompt.txt" RESP=".codex_ci/generator_response.log" PATCH=".codex_ci/generator_patch.diff"

  local CARD="${1:-}"
  if [[ -z "$CARD" ]]; then
    local matches=()
    shopt -s nullglob
    for path in documents/feature_cards/*.md; do
      if grep -Eq '^status:\s*proposed' "$path"; then
        matches+=("$path")
      fi
    done
    shopt -u nullglob
    if [[ "${#matches[@]}" -eq 0 ]]; then
      echo "[generator] No Feature Cards with status=proposed"
      return 1
    fi
    CARD="${matches[0]}"
  elif ! grep -Eq '^status:\s*proposed' "$CARD"; then
    echo "[generator] Card $CARD is not marked status: proposed"
    return 1
  fi

  echo "[generator] Generating specs for $CARD"

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
    echo "--- BEGIN AGENTS.md EXCERPT ---"; sed -n '1,300p' AGENTS.md; echo "--- END AGENTS.md EXCERPT ---"
    echo
    echo "--- BEGIN FEATURE CARD ---"; cat "$CARD"; echo "--- END FEATURE CARD ---"
  } > "$PROMPT"

  local model_arg=""; [[ -n "$MODEL" ]] && model_arg="--model $MODEL"
  if ! $CODEX_BIN exec $CODEX_FLAGS ${model_arg:+$model_arg} --cd "$ROOT" -- "$(cat "$PROMPT")" > "$RESP" 2>&1; then
    cat "$RESP" >&2
    return 2
  fi

  if ! python3 - "$RESP" "$PATCH" <<'PY'
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

patch = "\n\n".join(segments)
Path(patch_path).write_text(patch, encoding="utf-8")
PY
  then
    return 2
  fi

  if [[ ! -s "$PATCH" ]]; then
    echo "[generator] Codex response did not contain a usable diff"
    return 3
  fi

  if ! git apply --index "$PATCH"; then
    echo "[generator] git apply --index failed; retrying without --index"
    if ! git apply "$PATCH"; then
      echo "[generator] Failed to apply Codex diff"
      return 4
    fi
    git add tests documents/feature_cards || true
  fi
  echo "[generator] Specs added. Running pytest smoke…"
  . .venv/bin/activate
  pytest -q || true
  return 0
}
