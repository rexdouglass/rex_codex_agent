# rex_codex_agent · Operations Guide

This repository ships the **Codex-first automation scaffold** that installs via `./rex-codex`. It is deliberately opinionated:

- **Platform:** Linux shells (Bash 4+) or WSL.
- **Language/tooling:** Python projects (pytest, mypy, ruff, black, isort, flake8) with coverage ≥80% by default.
- **LLM:** OpenAI Codex invoked through `npx @openai/codex` (Node 18+). Discriminator LLM edits are opt-in (`DISABLE_LLM=1` by default).
- **Audit goals:**
  1. Maintain a folder named `for_external_GPT5_pro_audit/` in each working repository.
  2. After every interaction, commit and push the current state of the repository and drop into that folder a concatenated snapshot of every important script/markdown/readme file, each prefixed with its absolute path.
  3. Snapshots **must** be generated via the built-in helper (`rex_codex.utils.create_audit_snapshot(RexContext.discover())` or an equivalent CLI hook); never hand-roll or trim the audit output.
  4. Treat audits as part of the conversational handshake—produce a fresh snapshot at the end of every operator interaction before yielding control.
- **Self-development loop:** `bin/fake-codex`, `scripts/selftest_loop.sh`, and `scripts/smoke_e2e.sh` must stay executable and green. We dogfood the agent by reinstalling it into clean workspaces and running the generator → discriminator pipeline offline.

The Bash wrapper is now a shim; all orchestration lives in the Python package `rex_codex` so we can unit-test and extend behaviour without shell metaprogramming.

Keep these expectations visible—both docs and templates must reinforce them so future LLM audits stay aligned.

---

## Golden Path (from empty repo to green)

1. **Install wrapper (inside the target repo)**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/scripts/install.sh | bash
   ```
2. **Bootstrap guardrails and tooling**
   ```bash
   ./rex-codex init
   ./rex-codex doctor      # verify python/node/docker availability
   ```
3. **Author a Feature Card**
   - Use `./rex-codex card new` for a guided prompt (writes `documents/feature_cards/<slug>.md` with `status: proposed`).
   - If you hand-edit, keep the `status:` line intact and leave `## Links` / `## Spec Trace` empty—the generator appends to them.
4. **Generate deterministic specs**
   ```bash
   ./rex-codex generator            # loops with a critic until DONE (use --single-pass to exit early)
   ```
   The generator:
   - Keeps diffs under `tests/feature_specs/<slug>/…` (tests only) and appends links/trace in the card.
   - Prints a dashboard summarising the Feature Card (acceptance criteria, existing specs) and previews the diff with new/updated tests before applying patches so operators can follow along in one screen.
   - Enforces patch-size limits (default 6 files / 300 lines).
- Warns when cards exist but their `status:` values don't match the requested set (e.g. typos like `propsed`) so operators can repair metadata quickly.
- Runs an AST hermeticity scan that bans network, subprocess, clock, and entropy **calls** (`requests.get`, `subprocess.run`, `time.sleep`, `uuid.uuid4`, `os.urandom`, `secrets`, `numpy.random`…), plus unconditional skip/xfail.
- Tag every spec with its acceptance target using either `"""AC#<n> …"""` docstrings or `@pytest.mark.ac(<n>)`. The Spec Trace, HUD coverage bar, and audit snapshots rely on these markers to keep acceptance → tests → pass/fail traceable.
5. **Run the discriminator ladder**
   ```bash
   ./rex-codex discriminator --feature-only   # smoke/unit on the spec shard (pytest -x --maxfail=1)
   ./rex-codex discriminator --global         # full ladder (xdist auto, coverage ≥80%)
   ```
   Stages = health → tooling → smoke/unit → coverage → optional `pip-audit`/`bandit`/`build` → style/type (`black`, `isort`, `ruff`, `flake8`, `mypy`). Each pass now ends with a color summary (stage, result, duration, first failing line) plus a “next command” hint if anything failed. Logs + JUnit land in `.codex_ci/`. Successful passes are recorded in `rex-agent.json`.
6. **Iterate via the loop**
   ```bash
  ./rex-codex loop                # generator → feature → global
  ./rex-codex loop --explain      # preview planned stages before execution
  ./rex-codex loop --discriminator-only   # implement runtime without re-triggering generator
  DISABLE_LLM=0 ./rex-codex loop --discriminator-only   # or add --enable-llm to discriminator/loop for guarded runtime edits
  ```
   The loop finishes with a two-line scoreboard (generator vs discriminator) so operators immediately know which phase passed, warned, or failed.
   Every invocation also generates `for_external_GPT5_pro_audit/audit_<timestamp>.md`, stages all changes, and pushes the repository so external GPT5-Pro audits can start from the latest state.
   Monitor mode (`--ui monitor`, default) keeps the HUD in a single refreshed screen. Use `--ui snapshot` for a one-off frame or `--ui off` to suppress HUD output during scripted runs.
   Need the latest frame without attaching to TTY? Call the single-shot helpers (handy for `watch -d` in CI): `./bin/rex-codex hud generator --slug <slug>` and `./bin/rex-codex hud discriminator --slug <slug>`.
7. **Promote the Feature Card**
   - When the repo is green, edit the card to `status: accepted` (generator never changes statuses). Commit your changes.

> Reset sandbox? `./rex-codex burn --dry-run` → `./rex-codex burn -y` → `./rex-codex init`.

### Self-development loop (maintainers run this constantly)

- `bin/fake-codex` emulates Codex and emits deterministic, hermetic diffs under `tests/feature_specs/<slug>/`. Keep it executable and versioned with the agent.
- `scripts/selftest_loop.sh` resets `.selftest_workspace/`, installs the current checkout, runs two Feature Cards (`hello_greet`, `hello_cli`) through generator → discriminator, appends logs/status/spec listings/runtime code to the latest audit file, then removes the workspace (`SELFTEST_KEEP=1` preserves it for debugging).
- `scripts/smoke_e2e.sh` spins up a temp repo, installs the current checkout via `scripts/install.sh`, scaffolds the `hello_greet` and `hello_cli` Feature Cards, runs `./rex-codex loop --feature-only`, then executes the global discriminator sweep. Export `KEEP=1` while debugging to retain the workspace.
- Run the selftest loop before accepting PRs, bumping `VERSION`, or cutting releases; use the broader smoke harness to cross-check longer flows. Treat failures as blockers—they signal the agent can no longer bootstrap itself offline.
- After both loops pass, repeat the Golden Path manually in a new repo (your target project—e.g. the practice Pong game) to confirm end-to-end behaviour beyond the stub.

---

## Guardrails & Defaults

- **Tests-first:** generator only writes specs; runtime changes must be manual or pass the discriminator’s guarded LLM step.
- **Protected surfaces:** tests, Feature Cards, documents, CI configs, dependency manifests, tooling configs are hash-snapshotted before LLM edits—unauthorized changes are reverted.
- **Runtime allow-list:** discriminator LLM patches may only touch runtime directories (`src/`, detected packages). Non-runtime paths are rejected.
- **Patch-size budgets:** generator and discriminator enforce defaults of 6 files / 300 lines (override via `GENERATOR_MAX_FILES/LINES`, `DISCRIMINATOR_MAX_FILES/LINES`).
- **Determinism:** hermetic specs ban network/entropy/time/subprocess calls; `PYTHONHASHSEED=0` is exported for generator snapshots and discriminator runs; pytest stages use configurable timeouts.
- **Coverage-first:** `COVERAGE_MIN` defaults to 80%; targets default to `src/`. Optional gates activate with `PIP_AUDIT=1`, `BANDIT=1`, `PACKAGE_CHECK=1`.
- **Auto-style:** mechanical `ruff/black/isort` runs only on runtime targets (never tests/docs).
- **Mypy scope:** type checking defaults to runtime targets (`MYPY_TARGETS` or `COVERAGE_TARGETS`); set `MYPY_INCLUDE_TESTS=1` to include spec shards when required.
- **Concurrency:** generator, discriminator, and loop take `.codex_ci/*.lock` with Python advisory (`fcntl`) locks.
- **Telemetry:** `rex-agent.json` tracks active slug/card and discriminator success metadata for auditability.

---

## Command Reference (internal expectations)

| Command | Notes for maintainers |
|---------|----------------------|
| `init` | Must remain idempotent. Seeds templates, enforces deterministic tool versions (see `templates/requirements-dev.txt`). |
| `generator` | Keep prompt guardrails aligned with code filters. Never relax hermetic checks without updating docs/templates. |
| `discriminator` | Maintain stage banners, logging, and optional gate envs. Default LLM usage must stay disabled (`DISABLE_LLM=1`). |
| `loop` | Orchestrates generator → discriminator. Ensure flag passthrough stays consistent with docs. |
| `card` | CLI helper for card creation/listing/validation—keep prompts aligned with template README. |
| `status` / `logs` | Surface rex-agent.json metadata and `.codex_ci` tails; `logs` supports `--generator/--discriminator/--lines`. |
| `doctor` | Emit versions/paths for python/node/docker; add tooling here before relying on it elsewhere. |
| `burn` | Preserve `.git`, warn loudly, honour `--dry-run` / `--purge-agent`. |
| `uninstall` | `--force` skips the prompt; `--keep-wrapper` leaves the shim in place. |
| `self-update` | Default is **offline** (`REX_AGENT_NO_UPDATE=1`). Respect release tags (`VERSION`) when enabling `stable`. |

---

## Quick Command Cheatsheet

- `./rex-codex init` – seed guardrails and tooling (idempotent).
- `./rex-codex card new` – scaffold a Feature Card; `card list` / `card validate` keep hygiene tight.
- `./rex-codex install --force` – refresh the agent sources in-place and automatically rerun `init`/`doctor` (use `--skip-init` / `--skip-doctor` to opt out).
- `curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/scripts/install.sh | bash -s -- --force --channel main` – reinstall the latest agent snapshot from anywhere.
- `./rex-codex generator --tail 120` – replay Codex diffs and tail logs when the generator fails (add `--quiet` to silence).
- `./rex-codex discriminator --feature-only` / `--global` – run the shard or full ladder; add `--tail 120` (and `--quiet` if you want silence) during debug sessions.
- `./rex-codex loop --tail 120` – generator → feature shard → global sweep with inline diff previews (use `--quiet` to suppress diff chatter).
- `./rex-codex logs --generator --lines 200` – dump the latest generator response/patch without hunting for files.
- `GENERATOR_PROGRESS_SECONDS=5 ./rex-codex loop` – tighten the Codex heartbeat interval (default 15s) for long generator passes.
- `./rex-codex status` – inspect the active slug/card and last discriminator success metadata.
- `./rex-codex burn --yes` – reset the working tree (keeps `.git`; add `--purge-agent` to drop `.rex_agent`).
- `./rex-codex uninstall --force` – remove the agent (use `--keep-wrapper` to leave the shim).
- `scripts/selftest_loop.sh` – fast offline selftest that resets `.selftest_workspace/`, exercises the `hello_greet` and `hello_cli` Feature Cards, and appends logs/status/spec listings to the latest audit file (`SELFTEST_KEEP=1` preserves the workspace).
- `scripts/smoke_e2e.sh` – run the self-development loop offline; export `KEEP=1` to keep the temp repo when investigating failures.

## Documentation Duties

- Update this file, `README.md`, and templates in `templates/` whenever behaviour, defaults, or guardrails change.
- Keep the docs explicit that the agent is Python/Linux/Codex-specific—LLMs reviewing the repo should never infer cross-language support.

---

## Release Conventions

- Bump `VERSION` and tag (`vX.Y.Z`) for every behavioural/template change.
- Ensure `bin/rex-codex --help` matches documented commands.
- Include `.codex_ci/` logs (or summaries) in PRs/notes for traceability.
- Verify templates (`templates/AGENTS.md`, `templates/documents/feature_cards/README.md`, enforcement tests) reflect new behaviour before cutting a release.

Keep the guardrails tight, prefer explicit documentation, and remember every change should reduce ambiguity for future Codex audits.***
