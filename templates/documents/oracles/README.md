# Oracle Manifest

This directory tracks deterministic “oracle” checks that run alongside the
generator → discriminator loop.  Populate `oracles.yaml` to declare additional
test stages (BDD scenarios, property-based suites, contract fuzzers, mutation
gates, etc.).  Each entry describes the command to execute plus the
dependencies it needs; the `rex-codex` CLI reads the manifest and orchestrates
the runs automatically.

Keep the manifest close to the feature cards so every capability documents its
oracle coverage.  Treat it like code – review changes, keep it up to date, and
write short comments describing why each oracle matters.
