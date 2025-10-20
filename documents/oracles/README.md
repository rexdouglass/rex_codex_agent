# Oracle Registry

This repository now supports explicit oracle manifests.  The template lives at
`templates/documents/oracles/oracles.yaml`; copy it into this directory and
customise the commands to match the project under test.  When present, the
`rex-codex` loop will execute each declared oracle after the generator and
discriminator stages.
