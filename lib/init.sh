#!/usr/bin/env bash
# lib/init.sh
set -Eeuo pipefail

rex_cmd_init(){
  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  echo "[*] Bootstrapping Python venv (.venv)…"
  command -v python3 >/dev/null || { echo "python3 not found"; exit 3; }
  [[ -d .venv ]] || python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip >/dev/null
  # Core dev tools (keep light; projects can add more)
  python -m pip install pytest pytest-xdist black isort ruff flake8 mypy >/dev/null

  mkdir -p tests/enforcement documents/feature_cards
  # Seed templates if missing
  [[ -f AGENTS.md ]] || cp "$REX_SRC/templates/AGENTS.md" AGENTS.md
  [[ -f pytest.ini ]] || cp "$REX_SRC/templates/pytest.ini" pytest.ini
  [[ -f pyproject.toml ]] || cp "$REX_SRC/templates/pyproject.toml" pyproject.toml
  [[ -f mypy.ini ]] || cp "$REX_SRC/templates/mypy.ini" mypy.ini
  [[ -f conftest.py ]] || cp "$REX_SRC/templates/conftest.py" conftest.py
  [[ -f .flake8 ]] || cp "$REX_SRC/templates/.flake8" .flake8
  [[ -f documents/feature_cards/README.md ]] || cp "$REX_SRC/templates/documents/feature_cards/README.md" documents/feature_cards/README.md
  cp -rn "$REX_SRC/templates/tests/enforcement/." tests/enforcement/ 2>/dev/null || true

  cat > rex-agent.json <<'JSON'
{
  "stages": ["sanity","deps","specs","unit","style"],
  "llm": { "bin": "npx --yes @openai/codex", "flags": "--yolo", "model": "" },
  "update_on_run": true
}
JSON

  echo "[✓] Project initialized. Try: ./rex-codex loop"
}
