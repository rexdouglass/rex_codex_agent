# Rex Codex Guardrails

This repository follows a staged automation ladder that keeps default runs fast, deterministic, and offline. Treat **AGENTS.md** as the contract for every tool-assisted pass (human or automated).

## Runtime vs Tests
- Runtime code lives under `src/` or `app/` (project-specific) and never imports from `tests/`.
- Tests live in `tests/` (including `tests/enforcement/` and `tests/feature_specs/`).
- Public modules expose stable contracts; tests verify behaviour but must not be imported by runtime.

## Specs, Docs, and Types
- Public callables require a docstring with an executable spec (doctest-style example or pytest-style spec case).
- Add type hints to public functions and methods; DO NOT remove existing annotations.
- Keep specs deterministic and offline; prefer fixtures or local fakes over network/file IO.

## Offline by Default
- Test suite defaults to `SYNTHETIC_RUN_LEVEL=local`.
- Sleeping, random jitter, and network access are prohibited in tests unless explicitly allowed by fixtures.
- Enforcement tests ensure tests fail fast if network/time-based calls slip in.

## Staged Automation Ladder (Green Default Run)
Runs execute in order. Each stage emits a `Question -> Command -> PASS/FAIL` triple.

| Level | Purpose                         | Question ID | Command (canonical)                               |
|-------|---------------------------------|-------------|----------------------------------------------------|
| 00    | Repo & system health            | 00.1        | `git status -sb`                                   |
|       |                                 | 00.2        | `python3 --version`                                |
| 01    | Tooling & dependencies          | 01.1        | `python -c 'import pytest; print(pytest.__version__)'` |
| 02    | Inline spec smoke               | 02.1        | `pytest -q -k 'spec or doctest'`                   |
| 03    | Unit test grid (no DB)          | 03.1        | `pytest -q -m 'not django_db'`                     |
| 06    | Style & type gates               | 06.1        | `black . --check`                                  |
|       |                                 | 06.2        | `isort . --check-only`                             |
|       |                                 | 06.3        | `ruff check .`                                     |
|       |                                 | 06.4        | `flake8 .`                                         |
|       |                                 | 06.5        | `mypy .`                                           |

Stages 04–05 (DB/UI) are optional packs you can enable per project by extending `rex-agent.json`.

## LLM Collaboration Rules
- LLMs only run after mechanical fixes (ruff/black/isort) fail to go green.
- Prompts must include relevant sections of this file.
- LLM diff output should be minimal, improving the stage that failed without weakening tests.

## Feature Cards Workflow
1. Create cards in `documents/feature_cards/<slug>.md` with a dedicated line `status: proposed`.
2. Prefer `./rex-codex card new` to scaffold cards; if you hand-edit, leave `## Links` / `## Spec Trace` blank so the generator can append references.
3. Run `./rex-codex generator <path>` (or omit `<path>` to auto-select the first proposed card). The generator iterates with a critic until it returns `DONE` (use `--single-pass` to opt out).
3. Use `./rex-codex discriminator --feature-only` to verify the feature shard (pytest `-x --maxfail=1`), then `./rex-codex discriminator --global` (pytest `-n auto` when xdist is present)—or `./rex-codex loop` to chain generator → feature → global.
4. Update the card to `status: accepted` once tests ship.
5. Retire the card once behaviour is shipped and documented.

### Command Cheatsheet
- `./rex-codex install --force` – refresh the agent if the embedded sources drift.
- `./rex-codex init` – seed guardrails and tooling.
- `./rex-codex card new` – scaffold Feature Cards; `card list` / `card validate` keep hygiene tidy.
- `./rex-codex generator` – produce/iterate tests for the next Feature Card until the critic says DONE.
- `./rex-codex discriminator --feature-only` / `--global` – run the shard (fail-fast) or full ladder (xdist auto); inspect failures with `./rex-codex logs`.
- `./rex-codex loop` – generator → feature shard → global sweep (use `--each`, `--status accepted`, `--skip-feature`, or `--skip-global` to tweak).
- `./rex-codex status` – inspect the active slug/card and last discriminator success metadata.
- `./rex-codex burn --yes` – reset the working tree (keeps `.git` and, by default, `.rex_agent`).

Keep this document updated when expectations shift. The automation loop assumes these guardrails are authoritative.
