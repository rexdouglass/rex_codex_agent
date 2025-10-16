# Hello CLI

status: proposed

## Summary

Provide a simple command-line greeting that demonstrates the generator HUD.

## Acceptance Criteria

- Run with default arguments and print `Hello World`.
- Accept `--message` to override the greeting text.
- Support `--quiet` to suppress output entirely.

## Links

- (pending)

## Spec Trace

- [AC#1] "Run with default arguments and print `Hello World`."
  -> tests/feature_specs/hello_cli/test_cli.py::test_default_greeting
- [AC#2] "Accept `--message` to override the greeting text."
  -> tests/feature_specs/hello_cli/test_cli.py::test_message_override
- [AC#3] "Support `--quiet` to suppress output entirely."
  -> tests/feature_specs/hello_cli/test_cli.py::test_quiet_mode_suppresses_output

