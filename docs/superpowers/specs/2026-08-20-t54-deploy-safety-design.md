# T5.4 Server-Only Deploy Safety Design

## Scope

This lane closes the operational causes tracked by F-T54-27, F-T54-40/F-T54-52, and F-T54-61 without deploying or changing production. It makes an ESP server release self-contained, validates dotenv syntax without sourcing or displaying values, protects disk headroom with bounded image retention, and proves that a server-only recreate leaves the database and web containers untouched.

## Approach

The release package will include the reviewed database backup script, a Python dotenv validator, and a remote server-only transaction script. `deploy-vps.sh --server-only` remains the local SSH/SCP entry point, but delegates remote preflight and mutation to the packaged transaction script instead of constructing a broad Compose command.

Two alternatives were rejected:

1. Extending the existing remote one-liner would keep quoting, error ordering, and fixture testing fragile.
2. Running a full Compose release and relying on `--no-recreate` would still expose unrelated services to resolution and lifecycle behavior.

The selected helper has a narrow contract and can run against fake Docker/Compose fixtures.

## Safety Sequence

1. Validate the candidate local env file, if supplied, before any SSH/SCP operation.
2. Validate the existing remote env file with the packaged parser before creating release directories, uploading files, backing up data, loading images, changing symlinks, removing images, or running Compose.
3. Upload the package into a new release directory and validate its checksums.
4. Require the selected env file's non-secret `TBOT_SERVER_IMAGE` to equal the reviewed image reference recorded by the package.
5. Check root filesystem free bytes and free percentage. If either threshold is missed, delete only surplus images in the configured server repository while preserving the active server image and one distinct rollback image. Recheck and fail closed if thresholds remain unmet.
6. Run the packaged database backup script. The script obtains the database password only inside the database container and never prints it.
7. Load only the server image archive, update the `current` symlink, and run `compose up -d --no-deps tbot-esp32-server`.
8. Wait for the server replica health target, then compare database and web container IDs with the pre-mutation snapshot. Any change is a deployment failure.

## Secret Handling

The validator accepts dotenv assignment syntax, comments, blank lines, quoted values, and quoted multiline values. It rejects commands, invalid identifiers, trailing unquoted shell words, substitutions, and unterminated quotes. Diagnostics contain only a line number, optional variable name, and reason; values are never printed. The deployment scripts never `source` `/opt/tbot/.env`.

## Image Retention

Cleanup is limited to the configured server image repository. Active container IDs are resolved through Compose so every image ID used by scaled server replicas is retained. One additional newest distinct image ID is retained as rollback. Images used by any container are skipped. The incoming archive is not loaded until the post-cleanup free-space gate passes.

## Verification

Pytest fixtures execute the parser and remote transaction against fake `docker`, `df`, `sha256sum`, and backup commands. Coverage includes invalid assignments failing before mutation, secret redaction, active/rollback retention, insufficient-space refusal, exact `--no-deps` service targeting, unchanged database/web IDs, changed-ID rejection, and package contents. A dry-run test proves that no SSH/SCP command executes.
