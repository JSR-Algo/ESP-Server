# Task 07 Active-Lab Firmware Rollover Design

## Goal

Roll the physical-TFT preflight's authoritative active local-lab firmware source
from superseded SHA `aef1034f859b35efc93215106eb3be89f10f6c66` to reviewed SHA
`5b6121b7933cda25908cc5bd07f1b494f00728ca`.

## Contract

The preflight and attended-ledger validator continue to accept exactly one
active-lab firmware SHA. The new reviewed SHA replaces the old SHA in both
validators; neither introduces an allowlist or accepts arbitrary 40-hex source
identities. The application and bundle-root SHA-256 values remain exact
caller-supplied lowercase hashes and are not pinned to preliminary values.

All unrelated gates remain unchanged: production candidate identity, historical
NVS provenance, session NVS baseline, protected test identity and content hash,
Compose configuration, backend image provenance, local endpoints, synthetic
identities, output containment, and redaction.

## Tests And Documentation

Tests first supply the new firmware SHA and demonstrate that the current
preflight and ledger validators reject it. After the rollover, tests require
acceptance of the new SHA, rejection of the superseded SHA, and continued
acceptance of caller-supplied exact lowercase application and bundle-root hashes.
Authoritative Task 07 design, plan, HIL, and E2E references are updated to name
the new reviewed active-lab source without changing production firmware identity
or physical authorization.

No device, serial, network, Docker runtime, production, Farm, T54, T65, or
external-worktree action is part of this change.
