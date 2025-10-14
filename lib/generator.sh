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
    CARD="$(grep -l -E '^status:\s*proposed' documents/feature_cards/*.md 2>/dev/null | head -n 1 || true)"
    if [[ -z "$CARD" ]]; then
      echo "[generator] No Feature Cards with status=proposed"
      return 1
    fi
  fi

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
import sys
from pathlib import Path

resp, out = map(Path, sys.argv[1:3])
lines = resp.read_text(encoding='utf-8', errors='replace').splitlines()
keep_heads = (
    "diff --git ",
    "index ",
    "new file mode",
    "deleted file mode",
    "old mode",
    "new mode",
    "similarity index",
    "rename from",
    "rename to",
    "Binary files ",
    "--- ",
    "+++ ",
    "@@",
)
blocks, cur, include = [], [], False
for ln in lines:
    if ln.startswith("diff --git "):
        if cur and include:
            blocks.append("\n".join(cur))
        cur = [ln]
        include = False
        parts = ln.split()
        if len(parts) >= 4:
            path = parts[2][2:]
            include = path.startswith("tests/feature_specs/") or path.startswith("documents/feature_cards/")
        continue
    if not cur:
        continue
    if ln.startswith(keep_heads) or ln.startswith(("+", "-", " ")):
        cur.append(ln)
        continue
    if ln == "":
        cur.append(ln)

if cur and include:
    blocks.append("\n".join(cur))

Path(out).write_text("\n\n".join(blocks), encoding="utf-8")
PY
  then
    return 2
  fi

  if [[ ! -s "$PATCH" ]]; then
    echo "[generator] Codex response did not contain a usable diff"
    return 3
  fi

  git apply --index "$PATCH" || (git apply "$PATCH" && git add tests documents/feature_cards || true)
  echo "[generator] Specs added. Running pytest smoke…"
  . .venv/bin/activate
  pytest -q || true
  return 0
}
