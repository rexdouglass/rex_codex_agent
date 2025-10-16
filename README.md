# rex_codex_agent

Codex-first automation scaffold for **Python projects on Linux**. Drop the wrapper into a repo, describe work in Feature Cards, and the agent will:

- Generate **deterministic pytest specs** (tests only) from those cards.
- Run a disciplined **discriminator ladder** (smoke/unit → coverage ≥80% → optional security/package checks → style/type).
- Optionally nibble at runtime code with **tight guardrails** (small, allowlisted patches only).
- Capture logs, JUnit, and state in-repo so every pass is auditable.
- Dogfood itself with deterministic **self-development loops** (`scripts/selftest_loop.sh`, `scripts/smoke_e2e.sh`, and `bin/fake-codex`) so every change proves the generator → discriminator pipeline still works in a fresh repo.

> 🛠️ The agent intentionally targets **Linux shells (Bash 4+)**, **Python tooling**, and **OpenAI Codex** via `npx @openai/codex`. Windows support is via WSL; other ecosystems are out-of-scope.

`./rex-codex` is now a thin Bash shim that delegates to `python -m rex_codex`, so the orchestration logic (generator, discriminator, loop, card helpers) lives in Python modules that we can test and evolve directly.

---

## Requirements

- Linux (or WSL) with Bash 4+, `git`, and GNU `timeout` (Python handles advisory locks via `fcntl`).
- `python3` on PATH (the agent bootstraps a `.venv` with pytest/ruff/black/isort/flake8/mypy/pytest-cov).
- `node` 18+ if you want LLM-assisted generator/discriminator flows (the discriminator runs offline by default via `DISABLE_LLM=1`).
- Outbound network is optional: self-update now defaults **off** (`REX_AGENT_NO_UPDATE=1`). Flip to `0` to pull newer agent versions.
- For dogfooding, keep `bin/fake-codex` executable and run `scripts/selftest_loop.sh` (fast two-card loop) plus `scripts/smoke_e2e.sh` regularly—these harnesses prove the agent can install itself into a clean repo and go green without network access.

---

## Day-One Walkthrough

1. **Install the wrapper inside your repo**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/scripts/install.sh | bash
   ```

2. **Bootstrap guardrails and tooling** *(the install step now runs these automatically; rerun anytime for assurance)*
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
   - Each generator pass opens with a dashboard summarising the Feature Card (title, acceptance criteria, existing specs) and previews the proposed diff with per-test highlights before patches land.
- **Discriminator** executes the staged ladder (health, smoke/unit, coverage ≥80%, optional pip-audit/bandit/build, style/type).
  - Run just the feature shard: `./rex-codex discriminator --feature-only`
  - Run the full ladder: `./rex-codex discriminator --global`
- Runs now finish with a color-coded loop summary so you can see at a glance whether generator/discriminator passed, warned, or failed and why.
- After each run, an audit snapshot is written to `for_external_GPT5_pro_audit/` and committed/pushed automatically so GPT5-Pro reviews have the latest scripts and docs.
   - Add `--explain` to preview the planned generator/discriminator phases before they run; `--no-self-update` skips the preflight update check.
   - Need a targeted rerun? `./rex-codex discriminator --feature-only` handles the shard; `./rex-codex discriminator --global` runs the full ladder.
   - Monitor mode (`--ui monitor`, default) keeps a single refreshed HUD in the active terminal. When the command runs inside VS Code we also auto-launch a popout terminal so you can watch the HUD in a standalone window (override with `--ui popout`, `--no-popout`, or `GENERATOR_UI_POPOUT=0`). For the bundled `hello_…` specs we automatically scrub `tests/feature_specs/<slug>/` before each generator run so you always watch the toy project rebuilt from scratch; disable with `GENERATOR_SCRUB_SPECS=0` if you need to preserve prior artifacts. Prefer a static frame? Use `--ui snapshot`, or `--ui off` to silence HUD output entirely.
   - Popout HUD windows linger for ~30 s after completion so you can review the final state; tune via `GENERATOR_UI_LINGER`.
   - Grab the latest HUD frame without a TTY (perfect for `watch -d` or CI artifacts), or stream it live with `--follow`:
     ```bash
     ./bin/rex-codex hud generator --slug <slug>
     ./bin/rex-codex hud generator --slug <slug> --follow
     ./bin/rex-codex hud discriminator --slug <slug>
     ```
   - Need a consolidated single-window dashboard? The experimental Ink HUD prototype lives in `tui/`. It renders the structured NDJSON events described in the GPT‑5 GUI plan—follow the quick start in `tui/README.md` to run it against `.codex_ci/events.jsonl` or any compatible log.

5. **Implement runtime code until green**
   - Edit modules under `src/...` (or your package directories).
   - Re-run `./rex-codex loop --discriminator-only` for fast feedback.
   - Set `DISABLE_LLM=0` or add `--enable-llm` to allow the discriminator to propose tiny guarded runtime patches (requires `node`).

6. **Accept the feature**
   - When the discriminator is green, manually change the card to `status: accepted` and commit your work.

7. **Maintenance & lifecycle**
   - `./rex-codex status` – inspect the active slug/card and last discriminator success.
   - `./rex-codex logs` – tail the latest discriminator/generator output from `.codex_ci/`.
   - `./rex-codex card list` – list cards by status for quick triage.
   - `./rex-codex card rename <old> <new>` / `card split` / `card archive` / `card prune-specs` – keep Feature Cards and spec shards tidy without manual git plumbing.
   - `./rex-codex doctor` – diagnose env issues.
   - `./rex-codex install --force` – re-clone the agent and re-run `init`/`doctor` automatically (add `--skip-init` / `--skip-doctor` to opt out).
   - `./rex-codex burn --dry-run` then `--yes` – wipe repo contents (keeps `.git`, optionally `.rex_agent`).
   - `./rex-codex uninstall --force` – remove the agent (add `--keep-wrapper` to preserve the shim).

**Troubleshooting cheat sheet**
- `curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/scripts/install.sh | bash -s -- --force --channel main` – drop the latest agent into the current repo.
- `./rex-codex generator --tail 120` – replay Codex output and show the latest diff/log on failure (add `--quiet` to silence).
- `./rex-codex loop --tail 120` – run generator + discriminator with live diff previews and automatic log tails.
- `./rex-codex logs --generator --lines 200` – dump the most recent generator response/patch when you need manual inspection.
- `scripts/selftest_loop.sh` – fast offline selftest with two feature cards; export `SELFTEST_KEEP=1` to inspect `.selftest_workspace/`.
- `scripts/smoke_e2e.sh` – run the self-development loop end-to-end; set `KEEP=1` to preserve the temp repo for debugging.
- `GENERATOR_PROGRESS_SECONDS=5 ./rex-codex loop` – tighten the Codex heartbeat interval (default 15s) so long passes show more frequent progress updates.

**Focused troubleshooting**
- Tail without hunting: `./rex-codex logs --generator --lines 200` or `./rex-codex logs --discriminator --lines 200`.
- Follow logs live when debugging long runs: `./rex-codex logs --discriminator --follow`.
- Re-run a shard while iterating: `./rex-codex discriminator --feature-only --single-pass`.
- Promote to the full ladder when the shard is green: `./rex-codex discriminator --global`.
- Cap runaway stages when debugging: `./rex-codex discriminator --stage-timeout 180` (or pass via `loop --stage-timeout`).
- Keep LLM edits disabled by default (the loop exports `DISABLE_LLM=1`). Opt in with `./rex-codex discriminator --enable-llm --single-pass` or `DISABLE_LLM=0 ./rex-codex loop --discriminator-only` when ready.
- When a stage fails, read `.codex_ci_latest.log` for the first failing command and rerun the suggested “next command”.

---

## Command Overview

| Command | Purpose | Key Flags & Env |
|---------|---------|-----------------|
| `./rex-codex install` | Reinstall or refresh the agent in-place (auto-runs `init`/`doctor`). | `--force`, `--channel`, `--skip-init`, `--skip-doctor` |
| `./rex-codex init` | Seed `.venv`, guardrails, Feature Card scaffolding, and `rex-agent.json`. | — |
| `./rex-codex generator` | Generate deterministic pytest specs from the next `status: proposed` card. | `--single-pass`, `--max-passes`, `--focus`, `--status`, `--each`, `--tail`, `--quiet`, `--reconcile` |
| `./rex-codex discriminator` | Run the staged ladder (feature shard via `--feature-only`, full sweep by default). | `--feature-only`, `--global`, `--single-pass`, `--enable-llm`, `--disable-llm`, `DISCRIMINATOR_MAX_PASSES`, `COVERAGE_MIN`, `PIP_AUDIT`, `BANDIT`, `PACKAGE_CHECK`, `MYPY_TARGETS`, `MYPY_INCLUDE_TESTS`, `--tail`, `--quiet`, `--stage-timeout` |
| `./rex-codex loop` | Generator → feature shard → global sweep in one shot. | `--generator-only`, `--discriminator-only`, `--feature-only`, `--global-only`, `--each`, `--explain`, `--no-self-update`, `--enable-llm`, `--disable-llm`, `--tail`, `--quiet`, `--stage-timeout`, `--continue-on-fail` |
| `./rex-codex card` | Manage Feature Cards (`new`, `list`, `validate`, `rename`, `split`, `archive`, `prune-specs`). | `--status`, `--acceptance` (for `new`) |
| `./rex-codex status` | Show the active slug/card and last discriminator success. | `--json` |
| `./rex-codex logs` | Tail or follow the latest generator/discriminator logs from `.codex_ci/`. | `--generator`, `--discriminator`, `--lines`, `--follow` |
| `./rex-codex doctor` | Print versions/paths for `python3`, `node`, and `docker`. | — |
| `./rex-codex burn` | Wipe the repo (keeps `.git`; optional `--purge-agent`; supports `--dry-run`). | `--yes`, `--purge-agent`, `--dry-run` |
| `./rex-codex uninstall` | Remove `.rex_agent/` and optionally the wrapper. | `--force`, `--keep-wrapper` |
| `./rex-codex self-update` | Refresh the agent when `REX_AGENT_NO_UPDATE=0`. | `--channel`, `REX_AGENT_CHANNEL` |

### Exit codes at a glance

| Command | Exit | Meaning |
|---------|------|---------|
| `generator` | 0 | Specs updated successfully. |
| `generator` | 1 | No matching Feature Card (or card path missing). |
| `generator` | 2 | Codex CLI errored; inspect `.codex_ci/generator_response.log`. |
| `generator` | 3 | Diff rejected (paths or patch-size budget). |
| `generator` | 4 | Patch application failed; manual merge required. |
| `generator` | 5 | Critic returned empty guidance. |
| `generator` | 6 | Max passes reached without a `DONE`. |
| `generator` | 7 | Guardrail rollback (card edit or hermetic failure). |
| `discriminator` | 0 | Ladder passed. |
| `discriminator` | 1 | Stage failed or max passes reached. |
| `discriminator` | 2 | LLM disabled or runtime patch rejected (see latest log). |

Artifacts land in `.codex_ci/`:
- `latest_discriminator.log` / `.codex_ci_latest.log` – tail of the latest run.
- `generator_tests.log` – pytest snapshot of generated specs.
- `discriminator_feature_<slug>.xml`, `discriminator_global_smoke.xml`, `discriminator_global_unit.xml` – JUnit results.
The agent also tracks state in `rex-agent.json` (active slug/card, last discriminator success).

---

## Generator (tests only, never runtime)

- Discovers cards by status; prompt instructs the Codex CLI to output a **unified diff** limited to `tests/feature_specs/<slug>/…` and the matching card.
- Prints a concise dashboard before each pass (Feature Card summary, acceptance criteria, existing specs) and a diff summary that calls out new/updated tests so you can see the plan at a glance.
- Maintains a Spec Trace block linking each acceptance criterion to the generated tests and appends it to the card; use `./rex-codex generator --reconcile` to review coverage and orphaned specs without invoking the Codex CLI.
- Instrument spec files so coverage stays trustworthy: tag docstrings with `AC#<n>` or decorate tests with `@pytest.mark.ac(n)` to link them to acceptance bullets. Unmapped tests surface as orphans, and the HUD’s Feature Coverage Index (FCI) updates automatically as linked tests pass or fail.
- Warns when Feature Cards exist but their `status:` values miss the requested set (useful for catching typos like `propsed`).
- Before applying a diff it enforces:
  - Allowed-path filter.
  - Patch-size budget (`GENERATOR_MAX_FILES`, `GENERATOR_MAX_LINES`).
  - Hermeticity scan blocking network/clock/entropy/subprocess calls (e.g. `requests.get`, `subprocess.run`, `time.sleep`, `uuid.uuid4`, `secrets`, `numpy.random.*`).
  - Card guard: only appends in `## Links` / `## Spec Trace`, never mutates `status:`.
- After each pass it runs pytest on the spec shard and feeds logs to a “critic” loop until the card is marked `DONE` or max passes hit.
- Long Codex calls surface elapsed-time heartbeats (default every 15 seconds, configurable via `GENERATOR_PROGRESS_SECONDS`) so the loop never sits silent during a pass.
- Stores the last few pass durations and prints a quick ETA hint when recent iterations averaged ≥20 s, so slow Codex calls come with expectations.

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
- Every discriminator sweep ends with a colorized summary table (stage, identifier, duration, pass/fail) that includes the first failing log line and a suggested next command when something fails, making it easy to resume locally.

---

## Lifecycle Utilities & State

- `.rex_agent/` holds the agent sources; `.codex_ci/` holds run artifacts; `.codex_ci/*.lock` prevents concurrent commands from colliding.
- Templates (copied during `init`):
  - `AGENTS.md` – guardrails and operating guidance.
  - `documents/feature_cards/README.md` – how to structure cards.
  - `tests/enforcement/` – enforcement specs for repo hygiene.
- Self-update defaults off; set `REX_AGENT_NO_UPDATE=0` if you want automatic pulls (channels: `stable`, `main`, `<tag>`).

---

## Self-development Loop

- `bin/fake-codex` emulates `npx @openai/codex` and emits hermetic diffs limited to `tests/feature_specs/<slug>/`. Keep it executable so offline runs remain available.
- `scripts/selftest_loop.sh` resets `.selftest_workspace/`, installs the current checkout, exercises two feature cards (`hello_greet`, `hello_cli`) covering the default greeting and CLI flags, appends the command log/status/spec listing/runtime code to the latest audit file, then removes the workspace (set `SELFTEST_KEEP=1` to inspect).
- `scripts/smoke_e2e.sh` creates a throwaway repo, installs the current checkout via `scripts/install.sh`, scaffolds the `hello_greet` and `hello_cli` Feature Cards, and runs `./rex-codex loop --feature-only` followed by the global discriminator sweep (`KEEP=1` preserves the temp repo).
- Run the selftest loop before landing changes, bumping `VERSION`, or publishing docs; treat failures as release blockers. Follow up with the broader smoke harness as needed to validate longer paths.
- Once both pass, repeat the documented Golden Path in a fresh repo (e.g. your practice Pong game) to validate real-world usage with or without the Codex stub.
- Every selftest run appends its command log, generated sources, and discriminator outcomes to the latest `for_external_GPT5_pro_audit/audit_*.md` file. Leave that audit update in your commit so downstream reviewers (human or GPT5-Pro) can replay the evidence.

---

## Safety Rails & Defaults

- **Tests-first**: generator only writes specs; runtime edits must happen manually (or via the tightly constrained discriminator LLM pass).
- **Hermetic specs**: bans network/clock/entropy/subprocess calls, `skip`/`xfail`, and unseeded randomness.
- **Deterministic runs**: `PYTHONHASHSEED` defaults to `0`; pytest snapshots and discriminator stages honour configurable timeouts.
- **Patch-size limits**: generator and discriminator reject oversized diffs (defaults 6 files / 300 lines).
- **Protected paths**: tests, docs, configs, dependency manifests, CI, and the feature card are hashed before/after; unauthorized edits are reverted.
- **Coverage-first**: 80% minimum out of the box, captured via `pytest-cov`.
- **Optional gates**: enable `PIP_AUDIT=1`, `BANDIT=1`, `PACKAGE_CHECK=1` to bring security/build checks into the ladder.
- **Mypy scope**: type checking defaults to runtime targets (`MYPY_TARGETS` or `COVERAGE_TARGETS`); set `MYPY_INCLUDE_TESTS=1` to include spec shards when needed.
- **Concurrency-safe**: commands take out `.codex_ci/*.lock` using Python advisory (`fcntl`) locks.
- **Observability**: logs, JUnit XML, and recent state written to disk for CI ingestion and human review.

---

## Staying in the Guardrails

- The agent is purpose-built for **Python projects on Linux** with Codex as the LLM backend. Keep runtimes/tools in that lane for best results.
- When introducing new workflows or altering command behaviour, update `AGENTS.md`, this README, and the relevant templates before cutting a release.
- Version Tagged releases via `VERSION` ensure `REX_AGENT_CHANNEL=stable` installations stay reproducible.

Happy test-first hacking! For questions or contributions, open an issue/PR with the diff and attach the relevant `.codex_ci/` logs so reviewers can trace the run.***
