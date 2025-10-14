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
3. Draft a Feature Card (`documents/feature_cards/<slug>.md`) with a line that reads exactly `status: proposed` — the Codex CLI is ideal for this.
4. Turn the card into deterministic specs (the generator keeps iterating with a built-in critic until it declares `DONE`):
   ```bash
   ./rex-codex generator         # auto-selects the first proposed card; add --single-pass to opt out
   ```
5. Drive the staged ladder until it’s green:
  ```bash
  ./rex-codex discriminator --feature-only   # quick shard (fail-fast: pytest -x --maxfail=1)
  ./rex-codex discriminator --global         # full sweep (xdist -n auto when available)
  ```
  Use `./rex-codex loop` to execute steps 4 and 5 back-to-back (pass generator flags after `--`, e.g. `./rex-codex loop -- --single-pass`).
  Tune the discriminator with env vars when needed, e.g. `DISCRIMINATOR_MAX_PASSES=10 ./rex-codex discriminator --global`.

## Commands

- `./rex-codex init` – bootstrap `.venv`, guardrails, enforcement tests, Feature Cards.
- `./rex-codex generator` – iterate on the next `status: proposed` Feature Card until the critic returns `DONE` (tests land in `tests/feature_specs/<slug>/`).
- `./rex-codex discriminator --feature-only` – run the staged ladder against the active feature shard.
- `./rex-codex discriminator --global` – run the full staged ladder (default when no flag is given).
- `./rex-codex loop` – generator → feature shard → global sweep (use `--each-feature`, `--status`, `--skip-feature`, `--skip-global`, or `-- --single-pass` to tweak behaviour).
- `./rex-codex burn --yes` – wipe the working tree (keeps `.git`, the `rex-codex` wrapper, and by default `.rex_agent`).
- `./rex-codex doctor` – print environment diagnostics.

Logs land in `.codex_ci/` (e.g. `latest_discriminator.log`, `generator_tests.log`); `./rex-codex loop` keeps the compatibility tail in `.codex_ci_latest.log`.

## Templates

`lib/init.sh` seeds the `templates/` directory into target repositories, including:

- `AGENTS.md` – guardrails for runtime vs tests, specs, offline defaults, and stage ladder.
- `tests/enforcement/` – enforcement tests that defend doc/spec/type norms.
- `documents/feature_cards/` – guidance for capturing and progressing feature work.

## Development

This repository is pure Bash and templates; no `pip install` needed here. Update `VERSION` before cutting a tagged release so the installer can pin the latest stable revision.
