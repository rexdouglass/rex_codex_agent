# Generator HUD (Ink)

This package renders the structured generator event stream in a single terminal
window. It underpins the popout HUD launched by `./rex-codex` (the generator now
spawns this UI automatically when a popout is requested) and can also be run
manually for ad‑hoc inspection.

## Install once

```bash
cd /path/to/rex_codex_agent
npm --prefix tui install
```

The popout script (`scripts/start_hud_popout.sh`) performs this step lazily if
`node_modules/` is missing, but installing up front keeps the first launch snappy.

## Run manually

```bash
npm --prefix tui run start
```

By default the HUD tails `.codex_ci/events.jsonl`, focuses the first generator
slug it encounters, and reads the live diff from
`.codex_ci/generator_patch.diff`. Use the following environment variables to
override behaviour:

| Variable | Purpose |
|----------|---------|
| `TUI_EVENTS_FILE` | Path to the JSONL event stream (defaults to `<repo>/.codex_ci/events.jsonl`). |
| `TUI_DIFF_FILE` | Optional path to a diff preview (defaults to `<repo>/.codex_ci/generator_patch.diff`). |
| `TUI_SLUG` | Focus on a specific Feature Card slug (otherwise the first slug is used). |
| `TUI_REPO_ROOT` | Repository root; used when resolving relative paths for diff snapshots. |
| `TUI_PROJECT_TITLE` | Override the project title shown in the header. |

## What it shows

* **Outline** – Acceptance bullets (derived from the Feature Card) with a quick
  view of linked vs pending tests.
* **Coverage & Checks** – Spec→test linkage plus simple unit/integration meters
  alongside lint/type/build badges.
* **Event Log** – The last few generator events (feature/iteration/diff/spec‑trace).
* **Detail Pane** – Toggle with `t` to switch between diff, tests, and recent
  summaries.
* **Pending/Orphan tests** – Highlighted beneath the outline so coverage gaps
  are obvious before implementation starts.

The reducer ingests the existing generator events (`feature_started`,
`diff_summary`, `spec_trace_update`, `pytest_snapshot`, …) so no new upstream
schema is required. Additional generator events will surface automatically in
the log; extend `model.ts` if you want richer handling.

## Falling back

Set `GENERATOR_UI_TUI=0` (temporarily or in your environment) to revert to the
legacy text HUD. The popout script will detect the override and launch the
Python HUD instead.
