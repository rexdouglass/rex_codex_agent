# rex_codex_agent Design Review

This document captures the current architecture, the rationale for migrating orchestration logic from shell to Python, and the intended end-to-end user experience. It is meant to keep expectations visible for future audits.

## 1. Current Posture

- **Purposeful constraints:** Linux shells (Bash 4+ or WSL), Python projects and tooling (pytest, mypy, ruff, black, isort, flake8) with coverage >=80 percent, and Codex as the LLM backend via `npx @openai/codex`. The agent is intentionally Python/Linux/Codex-specific.
- **Golden Path** is already documented: install the wrapper, run `init` and `doctor`, author Feature Cards, generate deterministic specs, pass the discriminator ladder, iterate, and finally accept the card. Guardrails include hermetic tests, patch budgets, and deterministic defaults.
- **Shell commands** today: `init`, `generator`, `discriminator`, `loop`, `supervise`, `uninstall`, and a gated `self-update`.
  - `init` bootstraps `.venv`, seeds templates, writes `rex-agent.json`, and enforces deterministic tool versions.
  - `generator` produces deterministic pytest specs in `tests/feature_specs/<slug>/`, appends to the Feature Card links/trace sections, and enforces hermetic AST checks and patch budgets (defaults: 6 files, 300 lines).
  - `discriminator` runs a staged ladder (health, tooling, smoke/unit shards, coverage >=80 percent, optional pip-audit/bandit/build, then style/type). Mechanical fixes are limited to runtime paths, and LLM runtime edits are off by default (`DISABLE_LLM=1`).
  - `loop` orchestrates generator -> discriminator with Python advisory (`fcntl`) locking, and mirrors flag passthrough from the underlying commands.
  - `install` provides an in-place refresh path (`--force` re-clones the agent) to make recovery from broken installs obvious.
  - `supervise` is a thin wrapper over `loop`.
  - `uninstall` requires typing "remove agent" and honors `--keep-wrapper`.
  - `self-update` is opt-in and respects release channels via environment flags.

**Bottom line:** the current stack already delivers a disciplined, tests-first workflow with strong safety rails and reproducibility.

## 2. Architecture Direction (Shell vs. Python)

### Decision

Keep a thin Bash wrapper for installation ergonomics, but migrate orchestration logic into a Python package (`rex_codex`). The wrapper should drop into any repo and dispatch to `python -m rex_codex.cli`, preserving existing flags and behavior.

### Trade-offs

#### Bash (current)

- ✅ Minimal bootstrap friction; ideal for `curl | bash` installers; historical wrapper leveraged `flock`/`timeout`, while the Python CLI now owns locking via `fcntl`.
- ❌ Complex parsing, state management, and error handling are brittle; unit-testing is limited.
- ❌ The shell scripts already embed sizeable Python snippets (AST scanning, JSON edits), signalling that core logic wants a proper Python home.

#### Python (proposed)

- ✅ First-class support for testing, typing, logging, and state management; easier to express nuanced guardrails (protected-path hashing, hermetic scans, patch budgets).
- ✅ Enables richer UX (guided card creation, structured status/log outputs).
- ✅ Keeps guardrails centralized and testable.
- ❌ Requires Python to be present, but `.venv` bootstrapping already assumes it; retain the Bash shim to keep the drop-in experience.

### Recommendation

Adopt the hybrid approach: retain `./rex-codex` as a shell shim, but keep generator, discriminator, loop, doctor, burn, uninstall, and self-update inside the Python CLI so behavior can be tested and evolved safely.

## 3. User Journey (Idea -> Specs -> Runtime -> Quality Gates -> Iteration)

### Phase A: Idea to Deterministic Specs

1. **Install and bootstrap**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/rexdouglass/rex_codex_agent/main/scripts/install.sh | bash
   ./rex-codex init
   ./rex-codex doctor
   ```
   `init` creates `.venv`, installs dev tooling, copies templates (`AGENTS.md`, pytest/mypy configs, enforcement tests), and seeds `rex-agent.json`. Documentation highlights the Linux/Python/Codex scope and the Golden Path.
2. **Author a Feature Card**
   - Run `./rex-codex card new` or hand-edit `documents/feature_cards/<slug>.md`.
   - Keep `status: proposed` on its own line; leave `## Links` and `## Spec Trace` empty so the generator can append.
3. **Generate deterministic specs (tests only)**
   ```bash
   ./rex-codex generator
   ```
   - Writes tests to `tests/feature_specs/<slug>/`.
   - Enforces patch budgets (defaults: 6 files, 300 lines) and hermetic AST scan (blocks network/time/entropy/subprocess **calls**, yet allows deterministic imports; unconditional skip/xfail remain forbidden).
   - Appends references to the Feature Card but never modifies `status:`.

### Phase B: Implement Runtime and Pass the Ladder

4. **Run the discriminator ladder**
   ```bash
   ./rex-codex discriminator --feature-only
   ./rex-codex discriminator --global
   ```
   Stages: health -> tooling -> smoke/unit -> coverage (>=80 percent, targets default to `src/`) -> optional security/build gates -> style/type (`black`, `isort`, `ruff`, `flake8`, `mypy`). Artifacts (logs, JUnit) live under `.codex_ci/`.
5. **Iterate on runtime code**
   - Implement features inside runtime allowlists (`src/...` or detected packages).
   - Use `./rex-codex loop --discriminator-only` for tight feedback.
   - Mechanical formatters can auto-fix runtime files; LLM runtime edits remain opt-in (enable with `--enable-llm` or `DISABLE_LLM=0`) and heavily constrained.
   - Type checking defaults to runtime targets via `MYPY_TARGETS` / `COVERAGE_TARGETS`; set `MYPY_INCLUDE_TESTS=1` when you need to type-check generated specs.

### Phase C: Changing Scope or Refining Requirements

6. **Refine acceptance criteria**
   - Edit the card while it remains `status: proposed` (or include accepted cards via flags).
   - Re-run the generator; it updates specs within guardrails and appends card links/trace.
   - Run the discriminator to validate the new requirements.
7. **Split or merge scope**
   - Create additional cards as needed (`card new`).
   - Use generator/discriminator status filters (`--include-accepted`, `--status`) to revisit accepted work when needed.
8. **Reset sandbox or uninstall**
   - `./rex-codex burn --dry-run` -> `./rex-codex burn --yes` to reset (preserves `.git`, optional `--purge-agent`).
   - `./rex-codex uninstall --force` removes the agent without prompts; add `--keep-wrapper` to retain the shim for a reinstall.

## 4. Python CLI UX Enhancements

The Python CLI enables ergonomics that were cumbersome in shell:

1. **Guided Feature Card workflow** (`card new`, `card list`, `card validate`) with prompts and linting.
2. **Single "do the right thing" command** via `loop`, showing a summary of the planned generator/discriminator stages.
3. **Better observability** through `status` (renders `rex-agent.json`) and `logs` (tails `.codex_ci/` artifacts).
4. **Explicit self-update controls** (`self-update --channel`, `REX_AGENT_NO_UPDATE`, `REX_AGENT_CHANNEL`).
5. **Explain mode** (`loop --explain`) to preview guardrails, patch budgets, and planned stages before execution.
6. **Verbose/tail diagnostics** (`generator --verbose --tail`, `loop --verbose --tail`, `logs --generator/--discriminator`) so engineers can inspect Codex output without copying files manually.

## 5. Migration Plan

1. **Introduce the Python CLI package** (`rex_codex`) mirroring existing shell commands; re-home embedded Python snippets (AST scan, patch metrics, JSON state) into modules with tests.
2. **Convert `./rex-codex` into a thin shim** that locates the repo root, ensures Python is available, exports `PYTHONPATH` for the vendored sources, and calls `python -m rex_codex`.
3. **Expand UX** once parity is achieved: card helpers, `status`, `logs`, `self-update` surface, all while keeping default guardrails untouched (LLM off by default, coverage >=80 percent, patch budgets, hermetic specs).

## 6. Day-in-the-Life Scenario

1. Initialize and verify environment with `init` and `doctor`.
2. Create a card (`card new`) describing acceptance criteria.
3. Run `loop` to generate specs and execute the feature shard of the discriminator. Logs land in `.codex_ci/`.
4. Implement runtime code, rerunning `loop --discriminator-only` until green on feature and global stages.
5. Promote the card to `status: accepted` once the ladder passes.
6. When requirements change, update the card, rerun the generator, and iterate through the discriminator ladder again.
7. Optionally enable LLM runtime assistance by passing `--enable-llm` (or exporting `DISABLE_LLM=0`); guardrails still enforce runtime allowlists, patch budgets, and protected-path hashing. If Node is missing, the flow continues offline.
8. Use burn/uninstall flows to reset the environment or remove the agent entirely (`install --force` is available for re-cloning without a full uninstall).

## 7. Final Recommendation

- Adopt the hybrid architecture (shell shim + Python CLI) to align with the Python-first ecosystem, improve testability, and simplify future evolution.
- Preserve current guardrails and defaults: hermetic specs, protected paths, patch budgets, coverage >=80 percent, LLM disabled by default. These are the backbone of the tests-first, deterministic CI story and are reflected across README, AGENTS.md, and templates.
- Continue updating documentation (`README.md`, `AGENTS.md`, templates) whenever behavior or defaults change so future audits remain frictionless.
