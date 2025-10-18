# Feature Cards

Feature Cards describe upcoming Python work before implementation. Cards live beside this README and are consumed by the **Codex generator** to produce deterministic pytest specs. Keep them concise and machine-friendly.

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
<!-- generator may append references here -->

## Spec Trace
<!-- generator may append references here -->
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

> This project focuses exclusively on **Python projects running on Linux** with OpenAI Codex as the LLM backend. Cards should not describe non-Python or cross-platform work unless you can satisfy it within those bounds.
