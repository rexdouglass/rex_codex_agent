# Feature Cards

status: proposed

Feature Cards describe upcoming Python work before implementation. Cards live beside this README and are consumed by the **Codex generator** to produce deterministic pytest specs. Keep them concise and machine-friendly.

## Links
- tests/feature_specs/readme/test_feature_cards_template.py

## Spec Trace
- [AC#1] "deterministic checks a reviewer can run manually"
  -> [AC#1] tests/feature_specs/readme/test_feature_cards_template.py::test_ac1_happy_path_sections_are_parsed
  -> [AC#1] tests/feature_specs/readme/test_feature_cards_template.py::test_ac1_env_toggle_respects_root_env_override
  -> [AC#1] tests/feature_specs/readme/test_feature_cards_template.py::test_ac1_error_missing_card_raises_file_not_found
  -> [AC#1] tests/feature_specs/readme/test_feature_cards_template.py::test_ac1_error_missing_status_line_detected
  -> [AC#1] tests/feature_specs/readme/test_feature_cards_template.py::test_ac1_error_sections_require_bullets

## Required structure

```markdown
status: proposed   # required, on its own line (values: proposed|accepted|retired)
title: Concise summary
owner: optional-handle

## Summary
- short bullets describing the behaviour

## Acceptance Criteria
- deterministic checks a reviewer can run manually

## Links
- (pending)

## Spec Trace
- [AC#1] "deterministic checks a reviewer can run manually"
  -> [AC#1] tests/feature_specs/readme/test_feature_cards_template.py::test_ac1_happy_path_sections_are_parsed
  -> [AC#1] tests/feature_specs/readme/test_feature_cards_template.py::test_ac1_env_toggle_respects_root_env_override
  -> [AC#1] tests/feature_specs/readme/test_feature_cards_template.py::test_ac1_error_missing_card_raises_file_not_found
```

### Guidelines
- Always start with `status: proposed`. The generator will ignore cards without this exact line.
- Use bullets for Summary/Acceptance Criteria-Codex reads them into prompts verbatim.
- Leave `## Links` / `## Spec Trace` blank; the generator appends context there.
- Prefer deterministic acceptance criteria (no external network calls, no "eventually" language).
- Populate optional metadata (`id`, `epic`, `risk_level`, `priority`, `owner`, `dependencies`) when known-the generator canonicalises these fields and mirrors them into the assumption ledger.
- Capture explicit assumptions under an `## Assumptions` heading or inline using `A-###:` prefixes; unresolved ambiguity is turned into assumption ledger entries automatically.

## Day-one flow (per feature)

1. **Draft the card** following the template above (or run `./rex-codex card new` for an interactive scaffold).
2. **Generate specs**
   ```bash
   ./rex-codex generator documents/feature_cards/<slug>.md
   ```
   The generator will:
   - Create/update only `tests/feature_specs/<slug>/...` (pytest files).
   - Append references under `## Links` / `## Spec Trace`.
   - Reject non-deterministic tests (network, `time.sleep`, `uuid.uuid4`, `secrets`, unseeded randomness, etc.).
   - Enforce patch-size limits (default 6 files / 300 lines).
3. **Implement runtime code** under `src/...` (Python only) until the discriminator ladder is green.
4. **Promote the card** to `status: accepted` once the discriminator passes; commit the change alongside your runtime code.

Need to clean up formatting? `./rex-codex card lint --output json` surfaces machine-readable diagnostics, and `./rex-codex card fix` auto-inserts the required sections/status lines for common issues without touching acceptance content.

Environment toggles for local iteration:
- `REX_DISABLE_AUTO_COMMIT=1` while developing locally if you only want a snapshot without touching git state.
- `REX_DISABLE_AUTO_PUSH=1` when you need an audit committed without pushing upstream.

> This project focuses exclusively on **Python projects running on Linux** with OpenAI Codex as the LLM backend. Cards should not describe non-Python or cross-platform work unless you can satisfy it within those bounds.
