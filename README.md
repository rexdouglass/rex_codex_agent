# rex_codex_agent

Codex-first automation scaffold for **Python projects on Linux**. Drop the wrapper into a repo, describe work in Feature Cards, and the agent will:

- Generate **deterministic pytest specs** (tests only) from those cards.
- Run a disciplined **discriminator ladder** (smoke/unit → coverage ≥80% → optional security/package checks → style/type).
- Optionally nibble at runtime code with **tight guardrails** (small, allowlisted patches only).
- Capture logs, JUnit, and state in-repo so every pass is auditable.

> 🛠️ The agent intentionally targets **Linux shells (Bash 4+)**, **Python tooling**, and **OpenAI Codex** via `npx @openai/codex`. Windows support is via WSL; other ecosystems are out-of-scope.

`./rex-codex` is now a thin Bash shim that delegates to `python -m rex_codex`, so the orchestration logic (generator, discriminator, loop, card helpers) lives in Python modules that we can test and evolve directly.

---

## Requirements

- Linux (or WSL) with Bash 4+, `git`, `flock`, and GNU `timeout`.
- `python3` on PATH (the agent bootstraps a `.venv` with pytest/ruff/black/isort/flake8/mypy/pytest-cov).
- `node` 18+ if you want LLM-assisted generator/discriminator flows (the discriminator runs offline by default via `DISABLE_LLM=1`).
- Outbound network is optional: self-update now defaults **off** (`REX_AGENT_NO_UPDATE=1`). Flip to `0` to pull newer agent versions.

---

## Day-One Walkthrough

1. **Install the wrapper inside your repo**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/scripts/install.sh | bash
   ```

2. **Bootstrap guardrails and tooling**
   ```bash
   ./rex-codex init
   ./rex-codex doctor   # confirm python/node/docker availability
   ```

3. **Author a Feature Card**
   ```bash
   ./rex-codex card new       # guided prompts (writes documents/feature_cards/<slug>.md)
   ```
   Prefer the helper above—if you hand-edit, keep `status: proposed` on its own line and leave `## Links` / `## Spec Trace` empty so the generator can append to them later.
   The template in `templates/documents/feature_cards/README.md` shows the full heading layout the generator expects.

4. **Generate specs → run the ladder**
   ```bash
   ./rex-codex loop
   ```
   - **Generator** converts the card into deterministic pytest specs under `tests/feature_specs/<slug>/`.
   - **Discriminator** executes the staged ladder (health, smoke/unit, coverage ≥80%, optional pip-audit/bandit/build, style/type).

5. **Implement runtime code until green**
   - Edit modules under `src/...` (or your package directories).
   - Re-run `./rex-codex loop --discriminator-only` for fast feedback.
   - Set `DISABLE_LLM=0` to let the discriminator propose tiny guarded runtime patches (requires `node`).

6. **Accept the feature**
   - When the discriminator is green, manually change the card to `status: accepted` and commit your work.

7. **Maintenance & lifecycle**
   - `./rex-codex status` – inspect the active slug/card and last discriminator success.
   - `./rex-codex logs` – tail the latest discriminator/generator output from `.codex_ci/`.
   - `./rex-codex card list` – list cards by status for quick triage.
   - `./rex-codex doctor` – diagnose env issues.
   - `./rex-codex burn --dry-run` then `--yes` – wipe repo contents (keeps `.git`, optionally `.rex_agent`).
   - `./rex-codex uninstall` – remove the agent wrapper after typing “remove agent”.

---

## Command Overview

| Command | Purpose | Key Flags & Env |
|---------|---------|-----------------|
| `./rex-codex init` | Seed `.venv`, guardrails, Feature Card scaffolding, and `rex-agent.json`. | — |
| `./rex-codex generator` | Generate deterministic pytest specs from the next `status: proposed` card. | `--single-pass`, `--max-passes`, `--status`, `--each` |
| `./rex-codex discriminator` | Run the staged ladder (feature shard via `--feature-only`, full sweep by default). | `--global`, `--single-pass`, `DISCRIMINATOR_MAX_PASSES`, `DISABLE_LLM`, `COVERAGE_MIN`, `PIP_AUDIT`, `BANDIT`, `PACKAGE_CHECK` |
| `./rex-codex loop` | Generator → feature shard → global sweep in one shot. | `--generator-only`, `--discriminator-only`, `--feature-only`, `--global-only`, `--each` |
| `./rex-codex card` | `new`, `list`, `validate` helpers for Feature Cards. | `--status`, `--acceptance` (for `new`) |
| `./rex-codex status` | Show the active slug/card and last discriminator success. | — |
| `./rex-codex logs` | Tail the latest generator/discriminator logs from `.codex_ci/`. | — |
| `./rex-codex doctor` | Print versions/paths for `python3`, `node`, and `docker`. | — |
| `./rex-codex burn` | Wipe the repo (keeps `.git`; optional `--purge-agent`; supports `--dry-run`). | `--yes`, `--purge-agent`, `--dry-run` |
| `./rex-codex uninstall` | Remove `.rex_agent/` and optionally the wrapper. | `--yes`, `--keep-wrapper` |
| `./rex-codex self-update` | Refresh the agent when `REX_AGENT_NO_UPDATE=0`. | `--channel`, `REX_AGENT_CHANNEL` |

Artifacts land in `.codex_ci/`:
- `latest_discriminator.log` / `.codex_ci_latest.log` – tail of the latest run.
- `generator_tests.log` – pytest snapshot of generated specs.
- `discriminator_feature_<slug>.xml`, `discriminator_global_smoke.xml`, `discriminator_global_unit.xml` – JUnit results.
The agent also tracks state in `rex-agent.json` (active slug/card, last discriminator success).

---

## Generator (tests only, never runtime)

- Discovers cards by status; prompt instructs the Codex CLI to output a **unified diff** limited to `tests/feature_specs/<slug>/…` and the matching card.
- Before applying a diff it enforces:
  - Allowed-path filter.
  - Patch-size budget (`GENERATOR_MAX_FILES`, `GENERATOR_MAX_LINES`).
  - Hermeticity scan blocking network/clock/entropy APIs (`requests`, `subprocess`, `time.sleep`, `uuid.uuid4`, `secrets`, `numpy.random…`, etc.).
  - Card guard: only appends in `## Links` / `## Spec Trace`, never mutates `status:`.
- After each pass it runs pytest on the spec shard and feeds logs to a “critic” loop until the card is marked `DONE` or max passes hit.

---

## Discriminator (quality ladder + guarded fixes)

Stages (feature or global):
1. Repo/system health (`git status -sb`, interpreter versions).
2. Tooling sanity (`python -c 'import pytest'`).
3. Smoke/unit grids (`pytest …`, parallel via `-n auto` when xdist present).
4. Coverage (default `COVERAGE_MIN=80`, targets default to `src/`).
5. Optional security/build gates (`pip-audit`, `bandit`, `python -m build` + `twine check`) driven by env flags.
6. Style/type (`black --check`, `isort --check-only`, `ruff check`, `flake8`, `mypy`).

Guardrails:
- Mechanical fixes (ruff/black/isort) run on runtime code only and auto-commit if they change anything.
- LLM runtime edits are **opt-in** (`DISABLE_LLM=0`) and obey protected-path hashing, runtime allowlists, patch-size limits, and “no shrinking tests”. Non-compliant diffs are reverted automatically.
- Each successful pass records a timestamp/slug/test-count in `rex-agent.json` for auditability.

---

## Lifecycle Utilities & State

- `.rex_agent/` holds the agent sources; `.codex_ci/` holds run artifacts; `.codex_ci/*.lock` prevents concurrent commands from colliding.
- Templates (copied during `init`):
  - `AGENTS.md` – guardrails and operating guidance.
  - `documents/feature_cards/README.md` – how to structure cards.
  - `tests/enforcement/` – enforcement specs for repo hygiene.
- Self-update defaults off; set `REX_AGENT_NO_UPDATE=0` if you want automatic pulls (channels: `stable`, `main`, `<tag>`).

---

## Safety Rails & Defaults

- **Tests-first**: generator only writes specs; runtime edits must happen manually (or via the tightly constrained discriminator LLM pass).
- **Hermetic specs**: bans network/clock/entropy APIs, `skip`/`xfail`, and unseeded randomness.
- **Deterministic runs**: `PYTHONHASHSEED` defaults to `0`; pytest snapshots and discriminator stages honour configurable timeouts.
- **Patch-size limits**: generator and discriminator reject oversized diffs (defaults 6 files / 300 lines).
- **Protected paths**: tests, docs, configs, dependency manifests, CI, and the feature card are hashed before/after; unauthorized edits are reverted.
- **Coverage-first**: 80% minimum out of the box, captured via `pytest-cov`.
- **Optional gates**: enable `PIP_AUDIT=1`, `BANDIT=1`, `PACKAGE_CHECK=1` to bring security/build checks into the ladder.
- **Concurrency-safe**: commands take out `.codex_ci/*.lock` via `flock`.
- **Observability**: logs, JUnit XML, and recent state written to disk for CI ingestion and human review.

---

## Staying in the Guardrails

- The agent is purpose-built for **Python projects on Linux** with Codex as the LLM backend. Keep runtimes/tools in that lane for best results.
- When introducing new workflows or altering command behaviour, update `AGENTS.md`, this README, and the relevant templates before cutting a release.
- Version Tagged releases via `VERSION` ensure `REX_AGENT_CHANNEL=stable` installations stay reproducible.

Happy test-first hacking! For questions or contributions, open an issue/PR with the diff and attach the relevant `.codex_ci/` logs so reviewers can trace the run.***
