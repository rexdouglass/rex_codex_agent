# Hello CLI

status: proposed

## Summary

Expose CLI flags so the greeting can be customised without editing the code.

## Acceptance Criteria

- Accept `--message` to override the greeting text.
- Return a zero exit status when the CLI succeeds.
- Support `--quiet` to suppress output entirely.

## Links

- (pending)

## Spec Trace

- [AC#1] "Accept `--message` to override the greeting text."
  -> tests/feature_specs/hello_cli/test_cli.py::test_message_override
  -> tests/feature_specs/hello_cli/test_cli.py::test_message_flag_requires_value
- [AC#2] "Return a zero exit status when the CLI succeeds."
  -> (pending)
- [AC#3] "Support `--quiet` to suppress output entirely."
  -> tests/feature_specs/hello_cli/test_cli.py::test_quiet_mode_suppresses_output
