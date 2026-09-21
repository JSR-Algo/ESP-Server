# Canonical backend test prerequisites

The backend test lane retains the repository's original `npm test -- --no-cache`
command. Its retained-media tests require a real PostgreSQL instance, the native
firmware callback and the independently published portal contract. Missing
prerequisites block qualification; focused tests are diagnostics only.

## Frozen inputs

Candidates running `backend-tests` provide this optional manifest extension:

```json
{
  "tools": {
    "backendTestInputs": {
      "portalOpenapi": {
        "path": "/absolute/trusted/portal/openapi.json",
        "sha256": "<64 lowercase hexadecimal characters>",
        "bytes": 12345
      }
    }
  }
}
```

Use the actual output of the backend's supported portal publisher. The file must
be regular, read-only, at a canonical trusted path, nonempty and at most 16 MiB.
The backend's own `openapi.json` is not a substitute for the published artifact.
The gate verifies size/hash and authority, stages a separate copy, rebases the
lane-local path and supplies `TBOT_PORTAL_OPENAPI_PATH`. Refresh the descriptor
after publishing changes.

## Owned native resources

Set `COURSE_MODE_NATIVE_TEST_SCRATCH_ROOT` to an absolute canonical trusted
directory on a volume with at least 128 MiB and five percent free. The gate
creates and removes only its own child directory. It uses the candidate's
verified Python runtime, firmware source, ESP source and pinned cJSON.

The current native build requires the root-owned macOS Command Line Tools
compiler at `/Library/Developer/CommandLineTools/usr/bin/clang` and its SDK.
Compiler identity is checked before and after execution. Missing tools remain
BLOCKED; another platform needs its own supported tool descriptor.

The lane creates an isolated PostgreSQL container from the candidate's exact
`database.engineImageId`, without pulling. A unique ownership label, verified
container ID and loopback-only ephemeral port bind its lifecycle. The generated
password is passed through the private environment, not command arguments.
Ambient retained database URLs do not select the database. Test exceptions and
timeouts still trigger owned-resource cleanup.

Native compilation failure is FAIL. Missing prerequisites and unverified
cleanup are BLOCKED. Cleanup failures retain resource identities for operator
recovery and must never be reported as a successful run. Do not remove a
container or scratch tree without verifying its recorded ownership.

The archived retained-media journey exercises the actual ESP materializer and
native callback with local fixture byte transport. It does not establish public
CDN access, radio performance, physical TFT quality or lesson completion.

Freeze dependency descriptors after any direct Vitest diagnostics: Vitest may
modify its node_modules result cache. Do not run direct tests concurrently with
canonical qualification against the same dependency tree.
