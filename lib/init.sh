#!/usr/bin/env bash
# lib/init.sh
set -Eeuo pipefail

rex_cmd_init(){
  if type rex_self_update >/dev/null 2>&1; then
    rex_self_update || true
  fi
  local ROOT; ROOT="$(rex_repo_root)"; cd "$ROOT"
  echo "[*] Bootstrapping Python venv (.venv)…"
  command -v python3 >/dev/null || { echo "python3 not found"; exit 3; }
  [[ -d .venv ]] || python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip >/dev/null
  local requirements_template="$REX_SRC/templates/requirements-dev.txt"
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
  "stages": ["sanity", "deps", "specs", "unit", "style"],
  "llm": { "bin": "npx --yes @openai/codex", "flags": "--yolo", "model": "" },
  "feature": {
    "active_card": null,
    "active_slug": null,
    "updated_at": null
  }
}
JSON

  echo "[✓] Project initialized. Try: ./rex-codex loop"
}
