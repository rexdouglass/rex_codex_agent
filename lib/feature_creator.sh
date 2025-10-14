#!/usr/bin/env bash
# lib/feature_creator.sh
set -Eeuo pipefail
rex_cmd_feature(){
  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  local CODEX_BIN="${CODEX_BIN:-npx --yes @openai/codex}"; : "${CODEX_FLAGS:=--yolo}"
  local MODEL="${MODEL:-}"
  mkdir -p .codex_ci
  local PROMPT=".codex_ci/feature_prompt.txt" RESP=".codex_ci/feature_response.log" PATCH=".codex_ci/feature_patch.diff"

  local CARD="${1:-}"
  if [[ -z "$CARD" ]]; then
    CARD="$(grep -l -E '^status:\s*proposed' documents/feature_cards/*.md | head -n 1 || true)"
    [[ -n "$CARD" ]] || { echo "No Feature Cards with status=proposed"; exit 1; }
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
  $CODEX_BIN exec $CODEX_FLAGS ${model_arg:+$model_arg} --cd "$ROOT" -- "$(cat "$PROMPT")" > "$RESP" 2>&1 || {
    cat "$RESP" >&2; exit 2;
  }

  python3 - "$RESP" "$PATCH" <<'PY'
import sys
from pathlib import Path
resp, out = map(Path, sys.argv[1:3])
lines = resp.read_text(encoding='utf-8', errors='replace').splitlines()
keep = ("diff --git ", "index ", "new file mode","deleted file mode","old mode","new mode","similarity index","rename from","rename to","Binary files ","--- ","+++ ","@@","+","-"," ","\\ No newline")
blocks, cur = [], []
for ln in lines:
    if ln.startswith("diff --git "):
        if cur: blocks.append("\n".join(cur)); cur=[]
        cur=[ln]
        toks=ln.split()
        if len(toks)>=4:
            a=toks[2][2:]
            if not (a.startswith("tests/feature_specs/") or a.startswith("documents/feature_cards/")):
                cur=[]
        continue
    if cur:
        if ln.startswith(keep) or ln=="":
            cur.append(ln)
        else:
            cur=[]
if cur: blocks.append("\n".join(cur))
text="\n\n".join(b for b in blocks if b)
Path(out).write_text(text, encoding='utf-8')
PY

  [[ -s "$PATCH" ]] || { echo "Codex response did not contain a test diff"; exit 3; }
  git apply --index "$PATCH" || (git apply "$PATCH" && git add tests documents/feature_cards || true)
  echo "[✓] Specs added. Running a quick smoke…"
  . .venv/bin/activate
  pytest -q || true
}
