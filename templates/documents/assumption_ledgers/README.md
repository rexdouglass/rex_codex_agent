# Assumption Ledgers

During `./rex-codex generator` runs the playbook writes card-specific assumption
ledgers here. Each ledger contains the assumption schema used by Codex:

- `assumption_id`, `text`, `rationale`, `risk`, `default_choice`, `ways_to_falsify`.
- `escalation_hints` for human follow-up.

Resolve ambiguity by editing the ledger and updating the originating Feature Card.
Reference assumption IDs (`A-###`) from docstrings, test names, and PR summaries so
traceability remains intact.
