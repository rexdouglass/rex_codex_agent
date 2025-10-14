#!/usr/bin/env bash
# Usage: curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/scripts/install.sh | bash
set -Eeuo pipefail

REPO_SLUG="${REPO_SLUG:-rexdouglass/rex_codex_agent}"
REPO_URL="${REPO_URL:-https://github.com/${REPO_SLUG}.git}"
CHANNEL="${REX_AGENT_CHANNEL:-stable}"   # stable|main|<tag>|<commit>

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

AGENT_DIR="$ROOT/.rex_agent"
SRC_DIR="$AGENT_DIR/src"
WRAPPER="$ROOT/rex-codex"

mkdir -p "$AGENT_DIR"

if [[ ! -d "$SRC_DIR/.git" ]]; then
  echo "[*] Cloning ${REPO_SLUG} into .rex_agent/src"
  git clone --depth 1 "$REPO_URL" "$SRC_DIR"
else
  echo "[*] Existing agent found; fetching updates"
  git -C "$SRC_DIR" fetch --all --tags --prune
fi

case "$CHANNEL" in
  stable)
    TAG="$(git -C "$SRC_DIR" tag --sort=-v:refname | head -n1)"
    TAG="${TAG:-main}"
    git -C "$SRC_DIR" checkout -q "$TAG"
    ;;
  main) git -C "$SRC_DIR" checkout -q main && git -C "$SRC_DIR" pull --ff-only ;;
  *)    git -C "$SRC_DIR" checkout -q "$CHANNEL" ;;
esac

cat > "$WRAPPER" <<'WRAP'
#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
REX_HOME="$ROOT/.rex_agent"
REX_SRC="$REX_HOME/src"
export REX_HOME REX_SRC ROOT
exec bash "$REX_SRC/bin/rex-codex" "$@"
WRAP
chmod +x "$WRAPPER"

echo "[✓] rex_codex_agent installed."
echo "Run: ./rex-codex init    # seed guardrails/tests/tooling"
echo "     ./rex-codex loop    # staged automation loop"
