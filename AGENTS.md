# rex_codex_agent · Operations Guide

This repository provides the Codex-friendly automation scaffold that target projects install via `./rex-codex`. Keep these guardrails in mind when modifying the agent or composing release notes.

## Golden Path (fresh practice repo)

1. **Install the agent wrapper**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/scripts/install.sh | bash
   ```
2. **Initialize guardrails and tooling**
   ```bash
   ./rex-codex init
   ```
3. **Author a Feature Card** under `documents/feature_cards/<slug>.md` with a dedicated line `status: proposed`.
4. **Generate enforcement specs** (generator loops with a critic until it reports `DONE`)
   ```bash
   ./rex-codex generator            # auto-selects the first proposed card (use --single-pass to opt out)
   ```
5. **Drive the staged tests and fixes**
   ```bash
   ./rex-codex discriminator
   ```
   (or run both steps together with `./rex-codex loop`).
6. **Iterate** until the discriminator reports a PASS, then update the card to `status: accepted`.

For a clean slate in a practice sandbox:
```bash
./rex-codex burn --yes      # keeps .git and .rex_agent by default
./rex-codex init            # reseed guardrails
```

## Repository Conventions

- **Versioning** – bump `VERSION` and retag (`vX.Y.Z`) for every behavioral or template change.
- **Command help** – keep `bin/rex-codex` help text synchronized with docs and template guidance.
- **Templates** – update `templates/AGENTS.md` and `templates/documents/feature_cards/README.md` whenever command names change.
- **Logging** – generator/discriminator/loop should emit clear stage banners so end users can trace progress.
- **Compatibility** – avoid breaking shell portability (Bash 4+), and keep dependencies limited to the Python stdlib plus the dev tools installed during `init`.

When introducing a new workflow, document it here, in `README.md`, and in the relevant templates before cutting a release.
