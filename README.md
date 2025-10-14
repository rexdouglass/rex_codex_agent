# rex_codex_agent

A portable Codex-first automation scaffold. Drop this repository into any Python project to bootstrap guardrails, enforcement tests, Feature Cards, and the staged loop you can run from the Codex CLI.

## Quick Start

1. Install the wrapper (inside the target repo):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/scripts/install.sh | bash
   ```
2. Seed guardrails and tooling:
   ```bash
   ./rex-codex init
   ```
3. Draft a Feature Card (`documents/feature_cards/<slug>.md`) with `status: proposed` — the Codex CLI is ideal for this.
4. Turn the card into deterministic specs:
   ```bash
   ./rex-codex generator         # auto-selects the first proposed card
   ```
5. Drive the staged ladder until it’s green:
   ```bash
   ./rex-codex discriminator
   ```
   (Use `./rex-codex loop` to execute steps 4 and 5 back-to-back.)

## Commands

- `./rex-codex init` – bootstrap `.venv`, guardrails, enforcement tests, Feature Cards.
- `./rex-codex generator` – convert a `status: proposed` Feature Card into deterministic pytest specs.
- `./rex-codex discriminator` – run the staged automation ladder (questions → commands → PASS/FAIL).
- `./rex-codex loop` – invoke generator and then discriminator until the repository is green.
- `./rex-codex burn --yes` – wipe the working tree (keeps `.git`, the `rex-codex` wrapper, and by default `.rex_agent`).
- `./rex-codex doctor` – print environment diagnostics.

## Templates

`lib/init.sh` seeds the `templates/` directory into target repositories, including:

- `AGENTS.md` – guardrails for runtime vs tests, specs, offline defaults, and stage ladder.
- `tests/enforcement/` – enforcement tests that defend doc/spec/type norms.
- `documents/feature_cards/` – guidance for capturing and progressing feature work.

## Development

This repository is pure Bash and templates; no `pip install` needed here. Update `VERSION` before cutting a tagged release so the installer can pin the latest stable revision.
