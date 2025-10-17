#!/usr/bin/env bash
# Usage: curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/packaging/install.sh | bash
set -Eeuo pipefail

REPO_SLUG="${REPO_SLUG:-rexdouglass/rex_codex_agent}"
REPO_URL="${REPO_URL:-https://github.com/${REPO_SLUG}.git}"
CHANNEL="${REX_AGENT_CHANNEL:-stable}"   # stable|main|<tag>|<commit>
FORCE="${REX_AGENT_FORCE:-0}"
SKIP_INIT="${REX_AGENT_SKIP_INIT:-0}"
SKIP_DOCTOR="${REX_AGENT_SKIP_DOCTOR:-0}"

usage() {
  cat <<'USAGE'
Usage: ./rex-codex install [--force] [--channel <ref>]
       curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/packaging/install.sh | bash -s -- [options]

Options:
  --force, -f       Remove any existing .rex_agent before reinstalling.
  --channel <ref>   Source ref to install (stable, main, tag, or commit).
  --skip-init       Do not run ./rex-codex init after installation.
  --skip-doctor     Do not run ./rex-codex doctor after installation.
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
    --skip-init)
      SKIP_INIT=1
      ;;
    --skip-doctor)
      SKIP_DOCTOR=1
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
if [[ -x "$REX_SRC/bin/rex-codex" ]]; then
  exec bash "$REX_SRC/bin/rex-codex" "$@"
else
  if [[ -d "$REX_SRC/src" ]]; then
    PYTHON_ROOT="$REX_SRC/src"
  else
    PYTHON_ROOT="$REX_SRC"
  fi
  export PYTHONPATH="${PYTHON_ROOT}:${PYTHONPATH:-}"
  exec python3 -m rex_codex "$@"
fi
WRAP
chmod +x "$WRAPPER"

echo "[✓] rex_codex_agent installed."

if [[ "$SKIP_INIT" != "1" ]]; then
  echo "[*] Running ./rex-codex init"
  if ! "$WRAPPER" init --no-self-update; then
    echo "[!] ./rex-codex init failed" >&2
    exit 1
  fi
  echo "[✓] ./rex-codex init completed."
else
  echo "[i] Skipped ./rex-codex init (requested)."
fi

if [[ "$SKIP_DOCTOR" != "1" ]]; then
  echo "[*] Running ./rex-codex doctor"
  if ! "$WRAPPER" doctor; then
    echo "[!] ./rex-codex doctor failed" >&2
    exit 1
  fi
  echo "[✓] ./rex-codex doctor completed."
else
  echo "[i] Skipped ./rex-codex doctor (requested)."
fi

echo "Next: ./rex-codex loop    # staged automation loop"
