# Project-Specific Notes

Use this file for repo-specific guardrails, workflow tips, or integration notes.
`./rex-codex init` seeds it once; subsequent runs leave your edits intact.

## Generator preflight guardrails

- `./rex-codex generator` exits with code `8` if the Codex CLI is misconfigured. Export `MODEL=<codex-model-id>` and run `npx @openai/codex login`; in interactive shells the generator will launch the login prompt if needed. API keys (e.g. `OPENAI_API_KEY`, `CODEX_API_KEY`) must remain unset.
- Exit code `9` indicates the Codex CLI timed out. Increase `CODEX_TIMEOUT_SECONDS` or re-run after confirming network access.
- Every generator run records a Codex "Hello World" probe. Check `rex-agent.json.preflight.codex_hello` or the monitor timeline for the latest stdout sample if you need to prove live connectivity.

## Release checklist helper

- Run `./rex-codex release` to create a timestamped checklist under `documents/release_plan/`. Use `--dry-run` to preview the steps without writing to disk.

## Oracle manifest notes

- Customise `documents/oracles/oracles.yaml` to wire repo-specific oracles (Behave scenarios, Hypothesis properties, Schemathesis/Dredd contracts, differential harnesses, Elle/Knossos workloads, etc.).
- `./rex-codex oracle --list` previews the configured suites; the main loop runs the manifest automatically once generator/discriminator finish.
