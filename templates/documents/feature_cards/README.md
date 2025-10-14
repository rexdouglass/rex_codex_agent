# Feature Cards

Feature Cards capture work before implementation. Each card lives in this directory and follows the template below:

```markdown
title: Concise summary
status: proposed|accepted|retired
owner: optional-handle
summary:
  - short bullets describing the behaviour
acceptance:
  - deterministic check people can run to verify
notes:
  - links or design context
```

## Flow
1. Start with a dedicated line `status: proposed`.
2. When specs/tests exist, run `./rex-codex generator documents/feature_cards/<slug>.md` (it keeps iterating with a critic until coverage is marked DONE; use `--single-pass` to opt out).
3. Use `./rex-codex discriminator --feature-only` for quick feedback, then `./rex-codex discriminator --global` (or `./rex-codex loop`; add `--each-feature --status accepted` to re-evaluate completed cards).
4. Review the generated pytest specs under `tests/feature_specs/`.
5. Update the card to `status: accepted` when the discriminator run succeeds, then retire once shipped and documented.
