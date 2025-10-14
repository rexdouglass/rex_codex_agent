# rex_codex_agent

A portable Codex-first automation scaffold. Drop this repository into any Python project to bootstrap guardrails, enforcement tests, Feature Cards, and the staged loop you can run from the Codex CLI.

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/scripts/install.sh | bash
./rex-codex init
./rex-codex loop
```

In a second terminal use the Codex CLI to draft documentation/specs, then run:

```bash
./rex-codex feature --no-review --run-loop
```

## Commands

- `./rex-codex init` – bootstrap `.venv`, guardrails, enforcement tests, Feature Cards.
- `./rex-codex loop` – run the staged automation ladder (questions → commands → PASS/FAIL).
- `./rex-codex feature` – convert a Feature Card into deterministic pytest specs.
- `./rex-codex supervise` – orchestrate feature creation followed by the loop.
- `./rex-codex burn --yes` – wipe the working tree (keeps `.git`, defaults to preserving `.rex_agent`).
- `./rex-codex doctor` – print environment diagnostics.

## Templates

`lib/init.sh` seeds the `templates/` directory into target repositories, including:

- `AGENTS.md` – guardrails for runtime vs tests, specs, offline defaults, and stage ladder.
- `tests/enforcement/` – enforcement tests that defend doc/spec/type norms.
- `documents/feature_cards/` – guidance for capturing and progressing feature work.

## Development

This repository is pure Bash and templates; no `pip install` needed here. Update `VERSION` before cutting a tagged release so the installer can pin the latest stable revision.
