# rex_codex_agent

A portable Codex-first automation scaffold. Drop this repository into any Python project to bootstrap guardrails, enforcement tests, Feature Cards, and the staged loop you can run from the Codex CLI.

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/scripts/install.sh | bash
./rex-codex init
./rex-codex loop

# Before running the generator, create at least one Feature Card with `status: proposed`
```

In a second terminal use the Codex CLI to draft documentation/specs. When a Feature Card is ready:

```bash
./rex-codex generator      # turn the next proposed card into enforcement tests
./rex-codex discriminator  # drive the staged ladder until the repo is green
# or
./rex-codex loop           # generator → discriminator in one shot
```

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
