#!/usr/bin/env bash
# Usage: curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/scripts/install.sh | bash
set -Eeuo pipefail

REPO_SLUG="${REPO_SLUG:-rexdouglass/rex_codex_agent}"
REPO_URL="${REPO_URL:-https://github.com/${REPO_SLUG}.git}"
CHANNEL="${REX_AGENT_CHANNEL:-stable}"   # stable|main|<tag>|<commit>
FORCE="${REX_AGENT_FORCE:-0}"

usage() {
  cat <<'USAGE'
Usage: ./rex-codex install [--force] [--channel <ref>]

Options:
  --force, -f       Remove any existing .rex_agent before reinstalling.
  --channel <ref>   Source ref to install (stable, main, tag, or commit).
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force|-f)
      FORCE=1
      ;;
    --channel)
      shift
      CHANNEL="${1:-$CHANNEL}"
      ;;
    --channel=*)
      CHANNEL="${1#*=}"
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
  shift || true
done

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

AGENT_DIR="$ROOT/.rex_agent"
SRC_DIR="$AGENT_DIR/src"
WRAPPER="$ROOT/rex-codex"

BACKUP_DIR=""
if [[ -d "$AGENT_DIR" && "$FORCE" == "1" ]]; then
  BACKUP_DIR="${AGENT_DIR}.bak.$(date +%s)"
  echo "[*] Removing existing agent (backup at ${BACKUP_DIR})"
  mv "$AGENT_DIR" "$BACKUP_DIR"
fi

mkdir -p "$AGENT_DIR"

cleanup() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
      echo "[!] Install failed; restoring previous agent."
      rm -rf "$AGENT_DIR"
      mv "$BACKUP_DIR" "$AGENT_DIR"
    fi
  else
    if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
      rm -rf "$BACKUP_DIR"
    fi
  fi
}
trap cleanup EXIT

if [[ ! -d "$SRC_DIR/.git" ]]; then
  echo "[*] Cloning ${REPO_SLUG} into .rex_agent/src"
  git clone --depth 1 "$REPO_URL" "$SRC_DIR"
else
  echo "[*] Existing agent found; fetching updates"
  git -C "$SRC_DIR" fetch --all --tags --prune --force
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
