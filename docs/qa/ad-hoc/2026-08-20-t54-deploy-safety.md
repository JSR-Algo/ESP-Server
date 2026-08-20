# T5.4 ESP Server-Only Deploy Safety

Date: 2026-08-20

Branch: `lesson-prod/t54-deploy-safety`

Scope: F-T54-27, F-T54-40/F-T54-52, and F-T54-61. This lane implements and tests release/deploy safety only. It does not deploy, connect to production, merge to main, push main, flash/reset hardware, create assignments, or navigate Android.

## Behavior

- `package-release.sh --server-only` saves only the server image and includes the reviewed `backup-db.sh`, `validate-env.py`, and `server-only-remote.sh` helpers in the checksum manifest.
- The package records the exact reviewed server image reference, and the transaction requires `TBOT_SERVER_IMAGE` to match it before mutation.
- The candidate env is validated locally. The existing remote env is validated by streaming the packaged parser over SSH before release-directory creation or upload. Values are not evaluated or displayed.
- The remote transaction revalidates both existing and candidate env files before Docker or filesystem mutation.
- Free bytes and free percentage are checked together. Cleanup runs only below threshold, considers only the server image repository, resolves scaled server replicas through Compose, preserves every active image ID plus one distinct rollback image, and skips images used by containers.
- The database backup obtains its password only inside the database container. Dry-run output contains no password command line.
- The deploy command is exactly `docker compose ... up -d --no-deps tbot-esp32-server`.
- Database and web container IDs are snapshotted and must remain byte-for-byte unchanged after the server health gate.

## RED Evidence

Command:

```sh
python3 -m pytest deploy/tests/test_deploy_safety.py -v
```

Initial result: 10 collected; 7 failed and 3 passed. Expected failures reported missing `deploy/validate-env.py`, missing `deploy/server-only-remote.sh`, and unsupported `--server-only` packaging/deploy behavior.

## GREEN Evidence

Focused verification after implementation and safety refinements:

```sh
bash -n deploy/deploy-vps.sh deploy/package-release.sh deploy/backup-db.sh deploy/server-only-remote.sh
python3 -m py_compile deploy/validate-env.py
python3 -m pytest deploy/tests/test_deploy_safety.py -v
```

Result after review fixes: 20 passed. Fixtures prove secret-redacted invalid assignment failure before Docker mutation, candidate rejection before transport, rejection of unquoted/double-quoted shell expansion while allowing literal single-quoted metacharacters, reviewed-image/env equality, two-replica active-image preservation plus one rollback, cleanup skipped when thresholds pass, persistent low-space refusal before backup/image load, exact server-only `--no-deps` targeting, stable DB/web IDs, changed-web-ID rejection, self-contained server-only and backward-compatible full-stack package manifests, host-secret-free backup execution, and transport-free dry-run output.

Compatibility and checked-in fixture verification:

```sh
python3 deploy/validate-env.py deploy/.env.example
python3 -m pytest main/tbot-server/tests/test_scaleout_deploy_topology.py -q
python3 -m ruff check deploy/validate-env.py deploy/tests/test_deploy_safety.py
git diff --check
```

Results: checked-in example validated with 55 assignments; deployment-topology suite 38 passed; Ruff clean; diff check clean.

## Code Review

Independent review of `a8072cab..9d2e5669` initially found two HIGH blockers: active server lookup assumed a fixed container name despite the scaled Compose service, and the env parser did not reject bare `$VAR` expansion. The follow-up resolves both with Compose-based multi-replica image discovery and fail-closed `$` handling outside single quotes. Focused re-review approved the delta with zero blocking findings; its local validation also reported 20 focused tests passed.
