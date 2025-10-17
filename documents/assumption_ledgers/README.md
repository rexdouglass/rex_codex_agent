# Assumption Ledgers

The generator now persists card-specific assumption ledgers under this folder. Each
ledger records:

- `assumption_id`, `text`, `rationale`, `risk`, `default_choice`, and
  `ways_to_falsify`.
- `escalation_hints` for product or QA follow-up.

Ledgers are updated every generator pass via `rex_codex.playbook`. Update them
manually when ambiguity is resolved so future runs stop carrying redundant
assumptions. Tests should reference relevant assumption IDs (`A-###`) in their
docstrings or markers.
