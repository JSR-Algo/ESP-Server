# M1 admission identity migration

The physical admission gate is pinned to the M0-selected LCDWiki ES3C35P
ESP32-S3 image and the Linux amd64 staging deployment. This is a source
migration, not a flash approval or a passing software qualification receipt.

| Input | Reviewed value |
| --- | --- |
| Firmware commit | `91c86074df5a17d5b684a6c28ea57b727abe3a01` |
| Application SHA-256 | `8531432b18eef2d656c5afb2574a2b73c2458679d355d6ebcb47828086b0dc95` |
| Application bytes | `3863248` |
| Evidence manifest SHA-256 | `39d1538b602829d472d017d78950baf46e8035bab1539e4a99871925732e2809` |
| Application offset / partition bytes | `0x20000` / `4128768` |
| Backend and web image platform | `linux/amd64` |

The image platform must match both the signed identity and observed Docker
metadata. Missing or wrong OS/architecture fails closed; later descriptor drift
invalidates publication. Database image identity retains its existing checks.

The approval key, operator UID, freshness window, exact robot identity, exclusive
serial lease, attended safety assertions, app-only/no-reset command, and all
protected partitions remain unchanged. Old signed admission documents cannot be
reused. Historical reports retain their original meanings.

Before hardware action, freeze a candidate containing this revision and the
matching images, pass every required software/full/live-database gate, create
fresh signed admission inputs, and run canonical physical admission. Obtain the
point-of-use authorization for that exact candidate and restore procedure. A unit
test PASS does not authorize opening serial or flashing.

The legacy schema-v3 post-flash receipt validator has separate historical source,
materializer and Compose bindings. This migration does not change those bindings
or allow relabeling historical receipts as current evidence. Any required receipt
migration needs its own failing regressions before generating current receipts.
