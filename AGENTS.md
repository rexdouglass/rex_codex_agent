# rex_codex_agent - Operations Guide

This repository ships the **Codex-first automation scaffold** that installs via `./rex-codex`. It is deliberately opinionated:

- **Platform:** Linux shells (Bash 4+) or WSL.
- **Language/tooling:** Python projects (pytest, mypy, ruff, black, isort, flake8) with coverage >=80% by default.
- **LLM:** OpenAI Codex invoked through `npx @openai/codex` (Node 18+). Discriminator LLM edits are opt-in (`DISABLE_LLM=1` by default).
- **Audit goals:**
  1. Maintain a folder named `for_external_GPT5_pro_audit/` in each working repository.
  2. After every interaction, commit and push the current state of the repository and drop into that folder a concatenated snapshot of every important script/markdown/readme file, each prefixed with its absolute path.
  3. Snapshots **must** be generated via the built-in helper (`rex_codex.utils.create_audit_snapshot(RexContext.discover())` or an equivalent CLI hook); never hand-roll or trim the audit output.
  4. Treat audits as part of the conversational handshake-produce a fresh snapshot at the end of every operator interaction before yielding control.
5. Before producing the final snapshot, run `scripts/selftest_loop.sh` so the repo proves it can regenerate the `hello_greet` / `hello_cli` specs end-to-end using the real Codex CLI (`npx @openai/codex`). The script appends its logs, status, and generated source listings to the latest audit file—commit that updated audit so external GPT5-Pro review sees the full trace.
     - Set `REX_DISABLE_AUTO_COMMIT=1` while developing locally if you only want a snapshot without touching git state.
     - Set `REX_DISABLE_AUTO_PUSH=1` when you need the audit committed but do not want the helper to push.
     - The agent automatically detects when it is running inside its own source tree and defaults to testing mode (auto commit/push disabled). Export `REX_AGENT_FORCE_BUILD=1` to override when you genuinely intend to publish from this repo.
- **Self-development loop:** `scripts/selftest_loop.sh` and `scripts/smoke_e2e.sh` must stay executable and green. We dogfood the agent by reinstalling it into clean workspaces, running the generator -> discriminator pipeline with the real Codex CLI, and ensuring the loop stays healthy.
- **Reward integrity:** Never substitute fake endpoints or local stubs for Codex in the toy project—the goal is to understand how the live model behaves. Any mitigation work must preserve the original prompts/responses in `.codex_ci/` so reviewers can examine raw interactions.
- **Monitor readiness:** The loop now blocks until the monitor UI responds on `/api/health`. If the default port is busy we automatically bump to the next free port and announce it—set `MONITOR_PORT` if you need a fixed value.
- **Codex timeouts:** Every Codex invocation enforces `CODEX_TIMEOUT_SECONDS` (default 300s). Heartbeats and timeout events stream into the monitor so long-running calls surface immediately; raise or lower the threshold via the env var when debugging.

The Bash wrapper is now a shim; all orchestration lives in the Python package `rex_codex` so we can unit-test and extend behaviour without shell metaprogramming.

Keep these expectations visible-both docs and templates must reinforce them so future LLM audits stay aligned.

> Repository-specific guardrails belong in `AGENTS.local.md`. This template is
> seeded alongside the global doc and never overwritten, so you can keep
> project-specific notes, integrations, and tribal knowledge there.

---

## Scope Boundaries

- **S0 – Global shim** (`bin/rex-codex`, `packaging/`): installers, uninstallers, and the thin CLI wrapper that dispatches into Python.
- **S1 – Project runtime** (`src/rex_codex/`, `project_runtime/`): pinned Python modules, templates, and manifest helpers copied into each consumer repo.
- **S2 – Sandbox** (`tests/e2e/`, `tests/unit/`, `tests/fixtures/`): hermetic self-tests that exercise the agent in throwaway repositories.

Treat each scope as a separately versioned surface: upgrade the global shim without disturbing existing projects, and evolve the sandbox without touching published runtimes.

---

## Golden Path (from empty repo to green)

1. **Install + bootstrap (inside the target repo)**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/packaging/install.sh | bash
   ```
   The installer strips audit/CI artefacts, always replaces any existing
   `.rex_agent/`, resets `.venv`, writes a pinned `requirements.txt`, and runs
   `./rex-codex init` then `./rex-codex doctor`. Re-run those commands manually
   later if you want to refresh guardrails/tooling checks.
3. **Author a Feature Card**
   - Use `./rex-codex card new` for a guided prompt (writes `documents/feature_cards/<slug>.md` with `status: proposed`).
   - If you hand-edit, keep the `status:` line intact and leave `## Links` / `## Spec Trace` empty-the generator appends to them.
4. **Generate deterministic specs**
   ```bash
   ./rex-codex generator            # loops with a critic until DONE (use --single-pass to exit early)
   ```
  Export `MODEL=<codex-model-id>` before invoking the generator and ensure the Codex CLI session is logged in (`npx @openai/codex login`). If the generator detects a logged-out state in an interactive shell it will launch the login flow for you. API-key authentication is disabled for this project. Optional environment knobs (`CODEX_TEMPERATURE`, `CODEX_TOP_P`, `CODEX_MAX_OUTPUT_TOKENS`, `CODEX_SEED`, `CODEX_REASONING_EFFORT`) are captured in `rex-agent.json` and surfaced by the monitor so runs stay reproducible—set them explicitly when you need deterministic sampling.
   The generator:
   - Performs a Codex preflight guard; `./rex-codex doctor` must report `status: OK` and `MODEL` must be set or the run aborts before contacting Codex.
   - Suggests matching runtime scaffolding for brand-new specs. Use `./rex-codex scaffold <slug>` to materialise the runtime skeleton before handing control to the discriminator.
   - Executes a "Hello World" Codex CLI probe so we have fresh evidence that the CLI answers prompts before any card-specific requests.
   - Keeps diffs under `tests/feature_specs/<slug>/...` (tests only) and appends links/trace in the card.
   - Prints a dashboard summarising the Feature Card (acceptance criteria, existing specs) and previews the diff with new/updated tests before applying patches so operators can follow along in one screen.
   - Enforces patch-size limits (default 6 files / 300 lines).
- Warns when cards exist but their `status:` values don't match the requested set (e.g. typos like `propsed`) so operators can repair metadata quickly.
- Runs an AST hermeticity scan that bans network, subprocess, clock, and entropy **calls** (`requests.get`, `subprocess.run`, `time.sleep`, `uuid.uuid4`, `os.urandom`, `secrets`, `numpy.random`...), plus unconditional skip/xfail.
- Tag every spec with its acceptance target using either `"""AC#<n> ..."""` docstrings or `@pytest.mark.ac(<n>)`. The Spec Trace, HUD coverage bar, and audit snapshots rely on these markers to keep acceptance -> tests -> pass/fail traceable.
5. **Run the discriminator ladder**
   ```bash
   ./rex-codex discriminator --feature-only   # smoke/unit on the spec shard (pytest -x --maxfail=1)
   ./rex-codex discriminator --global         # full ladder (xdist auto, coverage >=80%)
   ```
   Stages = health -> tooling -> smoke/unit -> coverage -> optional `pip-audit`/`bandit`/`build` -> style/type (`black`, `isort`, `ruff`, `flake8`, `mypy`). Each pass now ends with a color summary (stage, result, duration, first failing line) plus a "next command" hint if anything failed. Logs + JUnit land in `.codex_ci/`. Successful passes are recorded in `rex-agent.json`.
6. **Iterate via the loop**
   ```bash
  ./rex-codex loop                # generator -> feature -> global
  ./rex-codex loop --explain      # preview planned stages before execution
  ./rex-codex loop --discriminator-only   # implement runtime without re-triggering generator
  DISABLE_LLM=0 ./rex-codex loop --discriminator-only   # or add --enable-llm to discriminator/loop for guarded runtime edits
  ```
The loop finishes with a two-line scoreboard (generator vs discriminator) so operators immediately know which phase passed, warned, or failed.
Every invocation also generates `for_external_GPT5_pro_audit/audit_<timestamp>.md`, stages all changes, and pushes the repository so external GPT5-Pro audits can start from the latest state.
Monitor mode (`--ui monitor`, default) keeps the HUD in a single refreshed screen. When running inside VS Code we automatically spawn a companion terminal window for the HUD and keep it around for ~30 s after completion (`GENERATOR_UI_LINGER` tunes this). We also burn down the bundled `hello_*` spec shards before each generator run so the toy project is rebuilt from scratch every time; override with `--no-scrub-specs` or `GENERATOR_SCRUB_SPECS=0` if you need to preserve previous runs.
Keep the passive monitor (`monitor/server.js` on port 4321 by default) running during development; restart it promptly if the dashboard stops responding so discriminator/generator activity stays visible while we debug. Launch it with explicit absolute paths, e.g. `REPO_ROOT=$(pwd) LOG_DIR=$(pwd)/.codex_ci node monitor/server.js`, and sanity-check with `curl http://localhost:4321/api/summary` so we know the plan data is actually being served.
Need the latest frame without attaching to TTY? Call the single-shot helpers-or stream them live with `--follow` (generator only)-handy for `watch -d` in CI: `./bin/rex-codex hud generator --slug <slug> [--follow]` and `./bin/rex-codex hud discriminator --slug <slug>`.
- **Mandatory self-test:** Before landing major changes or handing off a session, run `scripts/selftest_loop.sh`. It rebuilds the toy `hello` project, regenerates both feature cards, drives the discriminator ladder, and appends the command log plus generated sources to the active audit file. Leave its output in place-external reviewers rely on that trace.

### Prompting Strategy Tracking

- For the toy project, every generator, critic, and discriminator call must use the real Codex CLI so we accumulate genuine behavioural data—no stubs, rewrites, or prompt short-circuits.
- Capture each prompt/response bundle in `.codex_ci/` (e.g. `generator_prompt.txt`, `generator_response.log`, `generator_patch.diff`, `latest_discriminator.log`). If a file is missing or empty, rerun the stage rather than trusting inferred success.
- When you tweak prompt wording, ordering, or command-line flags, document the change, the motivation, and observed outcomes in `AGENTS.local.md` (or the relevant Feature Card notes) so future operators know which strategies worked and which failed.
- Summarise the effective prompt strategies and any failure cases in the end-of-session audit note; this makes reward-hacking attempts obvious and gives downstream reviewers concrete artefacts to reproduce.
- Timeout events (`codex_timeout`) and one-shot prompts (`prompt_only_*`) are now logged automatically—treat them as datapoints when assessing whether a strategy is viable or needs revision.
7. **Promote the Feature Card**
   - When the repo is green, edit the card to `status: accepted` (generator never changes statuses). Commit your changes.

> Reset sandbox? `./rex-codex burn --dry-run` -> `./rex-codex burn -y` -> `./rex-codex init`.

### Self-development loop (maintainers run this constantly)

- Ensure `npx @openai/codex` is installed and reachable; the generator/discriminator loops run against the live Codex service.
- `scripts/selftest_loop.sh` resets `.selftest_workspace/`, installs the current checkout, runs two Feature Cards (`hello_greet`, `hello_cli`) through generator -> discriminator, appends logs/status/spec listings/runtime code to the latest audit file, then removes the workspace (`SELFTEST_KEEP=1` preserves it for debugging).
- `scripts/smoke_e2e.sh` spins up a temp repo, installs the current checkout via `packaging/install.sh`, scaffolds the `hello_greet` and `hello_cli` Feature Cards, runs `./rex-codex loop --feature-only`, then executes the global discriminator sweep. Export `KEEP=1` while debugging to retain the workspace.
- Run the selftest loop before accepting PRs, bumping `VERSION`, or cutting releases; use the broader smoke harness to cross-check longer flows. Treat failures as blockers-they signal the agent can no longer bootstrap itself offline.
- After both loops pass, repeat the Golden Path manually in a new repo (your target project-e.g. the practice Pong game) to confirm end-to-end behaviour beyond the toy project.

---

## Guardrails & Defaults

- **Tests-first:** generator only writes specs; runtime changes must be manual or pass the discriminator's guarded LLM step.
- **Protected surfaces:** tests, Feature Cards, documents, CI configs, dependency manifests, tooling configs are hash-snapshotted before LLM edits-unauthorized changes are reverted.
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
| `loop` | Orchestrates generator -> discriminator. Ensure flag passthrough stays consistent with docs. |
| `oracle` | Discover and execute declarative oracles declared in `documents/oracles/oracles.yaml`. |
| `card` | CLI helper for card creation/listing/validation-keep prompts aligned with template README. |
| `status` / `logs` | Surface rex-agent.json metadata and `.codex_ci` tails; `logs` supports `--generator/--discriminator/--lines`. |
| `doctor` | Emit versions/paths for python/node/docker; add tooling here before relying on it elsewhere. |
| `burn` | Preserve `.git`, warn loudly, honour `--dry-run` / `--purge-agent`. |
| `uninstall` | `--force` skips the prompt; `--keep-wrapper` leaves the shim in place. |
| `self-update` | Default is **offline** (`REX_AGENT_NO_UPDATE=1`). Respect release tags (`VERSION`) when enabling `stable`. |

---

## Quick Command Cheatsheet

- `./rex-codex init` - seed guardrails and tooling (idempotent).
- `./rex-codex card new` - scaffold a Feature Card; `card list` / `card validate` keep hygiene tight.
- `./rex-codex scaffold <slug>` - generate the runtime skeleton matching freshly generated specs.
- `./rex-codex install --force` - refresh the agent sources in-place and automatically rerun `init`/`doctor` (use `--skip-init` / `--skip-doctor` to opt out).
- `curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/packaging/install.sh | bash -s -- --force --channel main` - reinstall the latest agent snapshot from anywhere.
- `./rex-codex generator --tail 120` - replay Codex diffs and tail logs when the generator fails (add `--quiet` to silence).
- `./rex-codex discriminator --feature-only` / `--global` - run the shard or full ladder; add `--tail 120` (and `--quiet` if you want silence) during debug sessions.
- `./rex-codex loop --tail 120` - generator -> feature shard -> global sweep with inline diff previews (use `--quiet` to suppress diff chatter).
- `./rex-codex oracle --list` - review configured BDD/property/contract/metamorphic oracles; omit `--list` to execute them (respects `documents/oracles/oracles.yaml`).
- `./rex-codex logs --generator --lines 200` - dump the latest generator response/patch without hunting for files.
- `GENERATOR_PROGRESS_SECONDS=5 ./rex-codex loop` - tighten the Codex heartbeat interval (default 15s) for long generator passes.
- `./rex-codex status` - inspect the active slug/card and last discriminator success metadata.
- `./rex-codex burn --yes` - reset the working tree (keeps `.git`; add `--purge-agent` to drop `.rex_agent`).
- `./rex-codex uninstall --force` - remove the agent (use `--keep-wrapper` to leave the shim).
- `scripts/selftest_loop.sh` - fast two-card selftest that uses the live Codex CLI, resets `.selftest_workspace/`, exercises the `hello_greet` and `hello_cli` Feature Cards, and appends logs/status/spec listings to the latest audit file (`SELFTEST_KEEP=1` preserves the workspace).
- `scripts/smoke_e2e.sh` - run the self-development loop end-to-end with the live Codex CLI; export `KEEP=1` to keep the temp repo when investigating failures.
- `./rex-codex generator --prompt-file prompts/foo.txt --apply-target tests/feature_specs/hello_cli/test_cli.py` - run a headless, one-shot Codex prompt and ensure the returned diff touches the intended file.
- `./rex-codex release --dry-run` - print the release checklist; omit `--dry-run` to capture it under `documents/release_plan/`.

## Documentation Duties

- Update this file, `README.md`, and templates in `templates/` whenever behaviour, defaults, or guardrails change.
- Keep the docs explicit that the agent is Python/Linux/Codex-specific-LLMs reviewing the repo should never infer cross-language support.

---

## Release Conventions

- Bump `VERSION` and tag (`vX.Y.Z`) for every behavioural/template change.
- Ensure `bin/rex-codex --help` matches documented commands.
- Include `.codex_ci/` logs (or summaries) in PRs/notes for traceability.
- Verify templates (`templates/AGENTS.md`, `templates/documents/feature_cards/README.md`, enforcement tests) reflect new behaviour before cutting a release.
- Run `./rex-codex release` to generate a dated checklist and confirm the self-test loops pass before tagging.

Keep the guardrails tight, prefer explicit documentation, and remember every change should reduce ambiguity for future Codex audits.***

## Codex Testing Playbook

This playbook is implemented in `rex_codex.playbook` and drives the automated
conversion of Feature Cards into traceable, deterministic specs. Treat it as a
contract for how Codex plans, measures, and evolves tests.

### Oracle Portfolio: Ticket → Tests → Code

Every repo now carries `documents/oracles/oracles.yaml`, and `./rex-codex oracle`
executes the declared stages after the generator/discriminator loop. Populate the
manifest with the following oracle types (the template ships examples for each):

1. **Acceptance-criteria oracles (BDD/Gherkin).** Use Behave feature files to turn Feature Cards into executable scenarios (`features/`). Each scenario feeds the oracle stage via the `acceptance-bdd` entry.
2. **Property-based testing oracles.** Hypothesis suites (e.g. `tests/property/`) exercise algorithmic invariants; the manifest’s `property-tests` entry runs them.
3. **Metamorphic testing oracles.** Relational checks over multiple executions (`tests/metamorphic/`) catch oracle-hard domains such as search or ML outputs.
4. **Contract testing oracles.** Schemathesis fuzzing plus Dredd-style example validation ensure OpenAPI/GraphQL specs stay truthful (`contracts/api.yaml`).
5. **Differential testing oracles.** Back-to-back comparisons keep regressions visible when refactors land (`tests/differential/`).
6. **Runtime & temporal oracles.** LTL/state-machine monitors assert ordering and timing guarantees (`tests/monitors/`).
7. **Invariant mining oracles.** Re-run mined predicates (Daikon, Texada exports) to lock-in emergent behaviour (`tests/invariants/`).
8. **Concurrency/distributed oracles.** Elle/Knossos style workloads validate linearizability or transactional isolation (`tests/concurrency/`).
9. **LLM-assisted oracles.** Capture and audit model-suggested assertions or metamorphic relations; track them in the manifest for reproducibility.
10. **Mutation testing gate.** `mutmut` enforces a non-trivial mutation score before we trust the suite (`mutation-barrier` entry).

Each oracle can declare `required_paths`, `required_commands`, and `required_modules` so the stage skips gracefully when the supporting harness is absent. Use `./rex-codex oracle --list` to review what will run before committing.

### 0) Objectives & Non-Negotiables

**Primary goal:** Convert feature cards into a traceable, executable test suite that
captures intended behaviour, survives refactors, scales across interacting features,
improves monotonically, and stays fast and reliable in CI.

**Non-negotiables**

- Determinism over speed and repeatability over cleverness.
- Prefer public contracts (APIs, UI semantics, domain invariants) over internals.
- Traceability from card -> capability -> scenario -> observable -> test.
- Monotonic improvement: do not delete or weaken passing tests without intent.
- Default to parallel-safe execution; isolate or mark serial outliers.

### 1) Canonical Data Model

#### 1.1 Feature Card Canonicalization

Codex normalises every input card into a canonical schema:

```yaml
# FeatureCard.v1
id: FC-1234
title: "User can pause/resume a recurring transfer"
epic: "Payments - Scheduled Transfers"
risk_level: medium
priority: P1
owner: "payments-team"
version: 3
dependencies: [FC-1200, FC-1192]
acceptance_criteria:
  - id: AC-1
    text: "Pausing a transfer prevents runs until resumed."
  - id: AC-2
    text: "Resuming schedules pick up from the original cadence."
non_goals:
  - "Editing transfer amount during pause"
open_questions:
  - "What if resume date falls on a holiday?"
constraints:
  domain_invariants:
    - "Transfers cannot schedule in the past"
    - "Currency is immutable once transfer is created"
observability_hints:
  logs:
    - event: "transfer_schedule.paused"
    - event: "transfer_schedule.resumed"
  metrics:
    - counter: "transfers.paused_total"
    - counter: "transfers.resumed_total"
notes: "Existing cron-like scheduler; DB table schedules_v2"
```

Free-form cards that omit fields are captured with `unknown` placeholders plus
entries in the assumption ledger (see 2.2).

#### 1.2 Derived TestSpec Graph

Every card maps to a TestSpec graph for traceability:

```yaml
# TestSpec.v1
feature_card_id: FC-1234
capabilities:
  - id: CAP-1
    source_ac: AC-1
    statement: "Pause prevents execution"
    preconditions:
      - user_has_active_recurring_transfer
    triggers:
      - user_clicks_pause OR api_call_pause
    observables:
      - no_job_enqueued_for_next_tick
      - emitted_event: transfer_schedule.paused
      - ui_state_shows "Paused"
    negative_space:
      - cannot_execute_immediately_after_pause
    measurement_strategy:
      - "Inspect scheduler queue for next due date >= resume_date"
      - "Listen for event; assert exactly-once semantics"
    test_types: [unit, integration, e2e, property, contract]
    edge_cases:
      - "Pause within 1s of scheduled tick"
      - "Pause on holiday"
      - "Pause when already paused"
    invariants:
      - "Currency remains unchanged"
      - "Idempotent: repeated pause is no-op"
```

### 2) Resolve Ambiguity & Make It Testable

1. Decompose hierarchically: epic -> feature -> acceptance criterion -> capability
   -> scenario -> steps -> assertions.
2. Capabilities must stand alone (unit/property) and compose (integration/e2e).
3. Use cause-effect graphs and equivalence classes to minimise scenario counts.

#### 2.2 Assumption Ledger

Ambiguity is codified as explicit assumptions, never brushed aside. Each entry uses
`assumption_id`, `text`, `rationale`, `risk`, `default_choice`, and
`ways_to_falsify`. Embed assumption IDs in tests, docstrings, and PR summaries.
Maintain an escalation list for human follow-up.

Example:

```yaml
assumptions:
  - id: A-001
    text: "If resume date lands on a non-business day, schedule to next business day."
    rationale: "Aligns with existing settlement policy"
    risk: medium
    default_choice: "roll-forward"
    ways_to_falsify:
      - "OpenAPI spec contradicts"
      - "Existing prod logs show roll-back behavior"
```

### 3) Repository Intelligence & Code Mapping

Inventory the repo before generating tests: languages, frameworks, layout,
fixtures, helpers, selectors, API schemas, migrations, feature markers, and event
emitters. Build a mapping table of `capability_id -> code locations` for reuse.

### 4) Test Strategy Selection

Use a portfolio mindset. Prefer a few surgical e2e flows plus many rich
unit/property tests. Integration fills the seams. Contracts guard public APIs.

### 5) Observables & Measurement

Assert stable seams: API status/shape, event name + version, DB state without
volatile fields, `data-testid` selectors, relative timing windows, message queue
side-effects. Seed clocks and randomness. Limit snapshot tests to structural
shapes and versioned golden files.

### 6) Scenario Synthesis Algorithm

For each capability derive inputs, boundaries, negatives, and dependency
interactions. Prioritise by risk and priority. De-dupe by observable. See
`rex_codex.playbook._build_scenarios_for_capability` for the implementation.

### 7) Generate Tests

Follow Arrange-Act-Assert, stable IDs (`FC-XXXX-CAP-YY-SC-##`), reusable helpers,
and docstrings summarising assumptions/observables. Examples span unit, contract,
and UI e2e patterns with fake time.

### 8) Multi-Card Interactions

Maintain a constraint/interaction matrix across dependent cards. Generate composed
scenarios where behaviour overlaps. For conflicts, produce dual tests tagged with
the relevant assumption IDs.

### 9) Iteration, Parallelisation, and Isolation

Shard by component, keep fixtures layered, seed deterministic identifiers, and use
fake clocks. Resort to serial execution only when unavoidable.

### 10) Consistency with Existing Code

Add `data-testid` selectors rather than brittle locators. Raise contract drift
when schemas diverge. Validate events against the registry before asserting.

### 11) Improve Without Breaking

Preserve immutable test IDs, use explicit deprecation markers, produce semantic
diffs for golden updates, and run previous + new suites to guarantee monotonic
improvements.

### 12) Debugging Bad Tests

Classify failures (impossible, incorrect, flaky, dumb), minimise repro cases, fix
determinism, revisit assumptions, and run mutation tests to ensure assertions add
signal. Tag fixes with `@fixed`, quarantines with `@flaky-guarded`, gaps with
`@spec-gap(A-xxx)`.

### 13) Quality Gates

Enforce coverage, mutation score, flake rate, traceability completeness, and suite
runtime budgets. Promote maintenance by pruning redundant e2e tests.

### 14) CI/CD Integration

Pipeline order: lint -> unit/property -> contract -> integration -> e2e, with
fail-fast. Surfacing includes coverage deltas, new tests, assumptions, flake
history, and traceability tables. Attach diagnostics (screenshots, HAR, DB diffs,
event streams) on failure.

### 15) Templates & Checklists

- Traceability table (CSV) mirrors `test_id, feature_card, capability, scenario,
  observables, assumptions, test_type, components`.
- Optional Gherkin scenarios align with capability/scenario IDs.
- Test file header docstrings restate capability, scenario, assumptions, and
  observables. Pre-merge checklist covers selectors, fake time, assumption ledger,
  contract drift, and mutation score.

### 16) Property Testing Patterns

Use Hypothesis (Python) or quickcheck-like tools (Go) to encode calendar and
idempotency properties. Property tests replace bloated scenario enumerations.

### 17) Handling Legacy & Refactors

Wrap legacy endpoints with contract tests before refactors, keep UI selectors in a
registry, and add migration tests for DB changes (forward/backward compatibility).

### 18) Governance & Naming

- Test IDs: `FC-<num>-CAP-<num>-SC-<num>` and files mirror the ID.
- Tags: `@feature(FC-1234)`, `@component(scheduler)`, `@risk(high)`, `@serial`.
- Commit style: `test(FC-1234): add CAP-1 SC-03 pause prevents run`.

### 19) Failing Test Triage SOP

Check whether code changed, whether behaviour drifted, whether timing/env issues
exist, whether assertions are brittle, or whether the scenario is impossible.
Deprecate with justification when invariants prove it cannot happen.

### 20) Codex Agent Loop (High Level)

```text
for each FeatureCard:
  parse -> canonicalize
  build capability graph
  reconcile with repo mapping (APIs/UI/events/db)
  synthesise scenarios (equivalence + boundaries + negatives + interactions)
  select test portfolio (unit/property/contract/integration/e2e)
  generate tests with stable IDs + observables
  run locally with isolated fixtures + fake time
  triage failures (classify/repair)
  emit artifacts: tests, helpers, traceability, assumptions, PR summary
  commit with test impact analysis
```

### 21) Anti-patterns to Avoid

- Asserting private internals or brittle selectors.
- Time-based sleeps in place of waits or fake clocks.
- Snapshot sprawl without structural filters.
- E2E overload for edge cases better suited to unit/property tests.
- Coupling tests to global state or seeded IDs that leak across tests.

### 22) Minimal End-to-End Trace

Example mapping:

1. Card FC-1234 "Pause/Resume Recurring Transfer".
2. Capability CAP-1 "Pausing prevents next execution".
3. Scenario SC-03 "Pause just before tick".
4. Observables: `no job enqueued`, event `transfer_schedule.paused`.
5. Tests: unit (scheduler honours pause), contract (POST /pause schema), integration
   (pause -> scheduler -> no enqueue), e2e (UI pause with fake time).

**When in doubt**: document assumptions, assert behaviour at seams, use property
tests for generalised logic, keep e2e coverage sharp, and leave the suite cleaner
and more informative than you found it.
