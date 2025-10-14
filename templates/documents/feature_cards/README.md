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
1. Start with `status: proposed`.
2. When specs/tests exist, run `./rex-codex generator documents/feature_cards/<slug>.md`.
3. Use `./rex-codex discriminator` (or `./rex-codex loop`) to go green and capture fixes.
4. Review the generated pytest specs under `tests/feature_specs/`.
5. Update the card to `status: accepted` when the discriminator run succeeds, then retire once shipped and documented.
