#!/usr/bin/env bash
# Remove the global rex-codex shim and caches.
set -Eeuo pipefail

if command -v pipx >/dev/null 2>&1; then
  pipx uninstall rex-codex || true
fi

if command -v brew >/dev/null 2>&1; then
  brew uninstall rex-codex || true
fi

echo "Global shim removed. Run 'rex-codex uninstall --project' inside a repo to clean project state."
