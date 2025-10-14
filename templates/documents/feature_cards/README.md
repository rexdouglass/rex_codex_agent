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
2. When specs/tests exist, run `./rex-codex feature documents/feature_cards/<slug>.md`.
3. Review the generated pytest specs under `tests/feature_specs/`.
4. Update the card to `status: accepted` when the loop is green.
5. Retire the card once behaviour is shipped and documented.
