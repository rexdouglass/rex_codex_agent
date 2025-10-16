# Terminal HUD Prototype

This directory hosts an Ink + React terminal UI that renders structured agent
events in a single-screen layout. It is the first iteration of the GUI concept
outlined in the GPT‑5 design brief and is intended to be tailed alongside the
core `rex_codex` automation flows.

## Installation

```bash
cd tui
npm install
```

Dependencies are pinned in `package-lock.json`. The project uses TypeScript and
Ink 6 (ESM), so Node.js 18+ is required.

## Usage

1. Ensure a newline-delimited JSON event log is available. By default the HUD
   looks for `events.ndjson` in the current working directory. You can point it
   at any file by setting `TUI_EVENTS_FILE`.
2. Launch the HUD from an interactive terminal:

```bash
cd tui
TUI_EVENTS_FILE=examples/sample_events.ndjson npm start
```

Keyboard controls are only active when the process is attached to a TTY. When
running in a non-interactive environment the HUD falls back to a read-only
mode, but it will still render panes as events arrive.

## Event Schema

The reducer currently understands the following event shapes:

| `type`           | Fields                                                                 |
| ---------------- | ---------------------------------------------------------------------- |
| `decompose.ok`   | `feature_id`, `summary`, `details.created[]`                           |
| `test.proposed`  | `sub_id`, `tests[{id,type,desc}]`                                      |
| `test.frozen`    | `ids[]`, `summary`                                                     |
| `code.diff`      | `sub_id`, `path`, `diff`, `explain[]`                                  |
| `ci.result`      | `run_id`, `unit.{pass,fail}`, `lint`, `typecheck`                      |
| `summary.step`   | `short`, optional `long`                                               |
| `loop.signal`    | `level` (`green|yellow|red`)                                           |
| `needs.human`    | `reason`                                                               |

Unknown events are stored in the log but ignored by outline/coverage reducers,
so the UI can evolve as contracts tighten.

## Layout

The screen mirrors the design sketch:

- **Outline** – hierarchical FC/SC progress with per-node coverage roll-ups.
- **Health** – quick meters for spec→test mapping, unit/integration counts,
  lint/type/build results, and loop/novelty indicators.
- **Event Log** – six most recent structured events with timestamps.
- **Detail Pane** – cycles between diff, tests, and summary views (`t` key).

`examples/sample_events.ndjson` contains the scenario from the original design
doc and is handy for smoke testing UI tweaks without running the full agent.
