# rex-codex Project Runtime

This directory ships the artefacts that are copied into every target
repository under `./.agent/`. They are intentionally self-contained so the
per-project runtime can be bootstrapped, upgraded, or removed without a
network dependency.

Contents:

- `bootstrap.py` – utilities for creating the `.agent/` directory, writing the
  `agent.lock` manifest, and cleaning up generated files.
- `agent.lock.schema.json` – JSON Schema describing the lockfile format.
- `hooks/` – optional pre/post upgrade hooks. Kept empty so individual installs
  can opt-in to migrations.

Agents embed this folder into the generated workspace and call
`RuntimeBootstrapper.bootstrap()` whenever a project is initialised.
