# Rex Codex Guardrails

This repository follows a staged automation ladder that keeps default runs fast, deterministic, and offline. Treat **AGENTS.md** as the contract for every tool-assisted pass (human or automated).

## Runtime vs Tests
- Runtime code lives under `src/` or `app/` (project-specific) and never imports from `tests/`.
- Tests live in `tests/` (including `tests/enforcement/` and `tests/feature_specs/`).
- Public modules expose stable contracts; tests verify behaviour but must not be imported by runtime.
- `scripts/selftest_loop.sh` and `scripts/smoke_e2e.sh` stay executable; ensure `npx @openai/codex` is available and keep the self-development loops green before changes merge or releases cut.
- The monitor UI must respond on `/api/health` before generator/discriminator runs; the launcher auto-increments `MONITOR_PORT` when 4321 is busy so the HUD always comes up.

## Specs, Docs, and Types
- Public callables require a docstring with an executable spec (doctest-style example or pytest-style spec case).
- Add type hints to public functions and methods; DO NOT remove existing annotations.
- Keep specs deterministic and offline; prefer fixtures or local fakes over network/file IO.

## Offline by Default
- Test suite defaults to `SYNTHETIC_RUN_LEVEL=local`.
- Sleeping, random jitter, and network access are prohibited in tests unless explicitly allowed by fixtures.
- Enforcement tests ensure tests fail fast if network/time-based calls slip in.

## Oracle Manifest & CLI
- `./rex-codex init` seeds `documents/oracles/oracles.yaml`; customise it to wire BDD acceptance checks, Hypothesis properties, metamorphic relations, contract fuzzers, differential harnesses, runtime monitors, invariant replays, concurrency workloads, LLM-assisted assertions, and the `mutmut` gate.
- Run `./rex-codex oracle --list` to inspect the configured stages; omit `--list` to execute them on demand. `./rex-codex loop` runs the manifest automatically once the discriminator ladder completes.

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

Stages 04-05 (DB/UI) are optional packs you can enable per project by extending `rex-agent.json`.

## LLM Collaboration Rules
- LLMs only run after mechanical fixes (ruff/black/isort) fail to go green.
- Prompts must include relevant sections of this file.
- LLM diff output should be minimal, improving the stage that failed without weakening tests.
- Codex invocations are capped by `CODEX_TIMEOUT_SECONDS` (default 300s); raise/lower the env var when debugging but never disable guardrails without documenting why.

## Feature Cards Workflow
1. Create cards in `documents/feature_cards/<slug>.md` with a dedicated line `status: proposed`.
2. Prefer `./rex-codex card new` to scaffold cards; if you hand-edit, leave `## Links` / `## Spec Trace` blank so the generator can append references.
3. Run `./rex-codex generator <path>` (or omit `<path>` to auto-select the first proposed card). The generator iterates with a critic until it returns `DONE` (use `--single-pass` to opt out).
3. Use `./rex-codex discriminator --feature-only` to verify the feature shard (pytest `-x --maxfail=1`), then `./rex-codex discriminator --global` (pytest `-n auto` when xdist is present)-or `./rex-codex loop` to chain generator -> feature -> global.
4. Update the card to `status: accepted` once tests ship.
5. Retire the card once behaviour is shipped and documented.

### Command Cheatsheet
- `curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/packaging/install.sh | bash -s -- --force --channel main` - refresh the agent from the latest main snapshot.
- `./rex-codex install --force --channel main` - refresh the agent if the embedded sources drift (auto-runs `init`/`doctor`; add `--skip-init` / `--skip-doctor` to opt out).
- `./rex-codex init` - seed guardrails and tooling.
- `./rex-codex card new` - scaffold Feature Cards; `card list` / `card validate` keep hygiene tidy.
- `./rex-codex generator --tail 120` - iterate specs and print Codex diffs/logs on failure (add `--quiet` to silence).
- `./rex-codex discriminator --feature-only --tail 120` (or `--global`) - run the shard/full ladder with automatic log tails (add `--quiet` for silence).
- `./rex-codex loop --tail 120` - generator -> feature shard -> global sweep (use `--single`, `--status accepted`, `--skip-feature`, or `--skip-global` to tweak the queue).*** End Patch
- `./rex-codex logs --generator --lines 200` - dump the latest generator response/patch without spelunking.
- `.codex_ci/` holds the latest stage logs; use `./rex-codex logs --generator/--discriminator --lines 200` to surface them without poking in the filesystem.
- `./rex-codex status` - inspect the active slug/card and last discriminator success metadata.
- `./rex-codex burn --yes` - reset the working tree (keeps `.git` and, by default, `.rex_agent`).
- `./rex-codex uninstall --force` - remove the agent (pair with `--keep-wrapper` to leave the shim).
- `scripts/selftest_loop.sh` - run the fast two-card self-development loop against the live Codex CLI; export `SELFTEST_KEEP=1` to keep `.selftest_workspace/` for debugging.
- `scripts/smoke_e2e.sh` - run the end-to-end self-development loop with the live Codex CLI; export `KEEP=1` to keep the temp repo for debugging.
- `./rex-codex generator --prompt-file prompts/foo.txt --apply-target tests/feature_specs/<slug>/test_foo.py` - send a single prompt to Codex and ensure the diff touches the expected file.

## Self-development Loop
- Ensure `npx @openai/codex` is installed and reachable; the self-development loops exercise the live Codex service.
- `scripts/selftest_loop.sh` resets `.selftest_workspace/`, runs the `hello_greet` and `hello_cli` Feature Cards with the live CLI, appends logs/status/spec listings/runtime code to the latest audit file, and removes the workspace (`SELFTEST_KEEP=1` retains it for debugging).
  - Set `REX_DISABLE_AUTO_COMMIT=1` during local experiments if you need the snapshot only and want to skip committing.
  - Set `REX_DISABLE_AUTO_PUSH=1` to keep the commit but suppress the automatic push.
  - The agent auto-detects its own source tree and defaults to testing mode (no auto commit/push). Export `REX_AGENT_FORCE_BUILD=1` to override when deliberately publishing from the agent repo itself.
- `scripts/smoke_e2e.sh` provisions a temp repo, installs the current checkout, scaffolds the `hello_greet` and `hello_cli` Feature Cards, runs `./rex-codex loop --feature-only`, and executes the global discriminator pass. Set `KEEP=1` to retain the workspace for debugging.
- Run the selftest loop before merges, release tags, or documentation updates; use the smoke harness to validate the broader flow. Treat failures as blockers-they indicate the agent can no longer bootstrap itself locally.
- After both loops pass, repeat the Golden Path in your destination repo (e.g. your practice Pong game) to validate the workflow with real features.
- Every selftest invocation appends its command log and generated sources to the latest `for_external_GPT5_pro_audit/audit_*.md` snapshot-leave that audit update in place so external reviewers can replay the evidence.

Keep this document updated when expectations shift. The automation loop assumes these guardrails are authoritative.

## Codex Testing Playbook

This playbook is implemented in `rex_codex.playbook` and drives the automated
conversion of Feature Cards into traceable, deterministic specs. Treat it as a
contract for how Codex plans, measures, and evolves tests.

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
