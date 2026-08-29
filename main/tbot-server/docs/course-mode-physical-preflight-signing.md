# Course Mode physical preflight signing

The physical preflight accepts an expected-identity document only when its
canonical JSON bytes have a valid Ed25519 signature from the separately pinned
operator key. Tool provisioning and signing do not authorize flashing,
deployment, production mutation, or robot access.

## Immutable Darwin tools

First review the exact Docker and Compose source paths and hashes. Then an adult
operator may run the provisioning script through an attended `sudo` session:

```bash
./scripts/provision_course_mode_preflight_tools.sh --help
sudo ./scripts/provision_course_mode_preflight_tools.sh \
  --docker-source /Applications/Docker.app/Contents/Resources/bin/docker \
  --docker-sha256 '<reviewed-docker-sha256>' \
  --compose-source /Applications/Docker.app/Contents/Resources/cli-plugins/docker-compose \
  --compose-sha256 '<reviewed-compose-sha256>'
```

The installed identity paths are hash-addressed under
`/usr/local/libexec/tbot-preflight`. The script copies from verified open file
descriptors, installs `root:wheel` files with mode `0555`, rehashes them, and
checks every installed parent. It does not modify `Docker.app`.

Use one of these exact Apple Git implementations in the signed identity; never
use `/usr/bin/git`, a PATH lookup, Homebrew Git, or a symlink:

```text
/Library/Developer/CommandLineTools/usr/bin/git
/Applications/Xcode.app/Contents/Developer/usr/bin/git
```

The Command Line Tools path is the normal choice. The Xcode path is accepted
only when the file and every parent are root-owned, have no extended ACL, and
are not group/world writable. A default `/Applications` mode of `0775` therefore
makes the Xcode option fail closed; do not loosen that directory merely to make
preflight pass.

## Operator key ceremony

Do not run these commands until the operator gives explicit operator authority
at the point of use. The assignment that introduced this guide did not generate
a private key.

Choose an operator-controlled directory outside every repository, evidence
directory, cloud-synced folder, and task artifact root. Never place the private
key, unsigned identity draft, or secret material in Git or release evidence.

```bash
umask 077
export COURSE_MODE_OPERATOR_KEY_DIR=/absolute/operator-controlled/offline-directory
mkdir -p "$COURSE_MODE_OPERATOR_KEY_DIR"
python3 - "$COURSE_MODE_OPERATOR_KEY_DIR" <<'PY'
import os
import pathlib
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

directory = pathlib.Path(sys.argv[1])
private_path = directory / "course-mode-preflight-ed25519.pem"
public_path = directory / "course-mode-preflight-ed25519.raw.pub"
private_key = Ed25519PrivateKey.generate()
private_bytes = private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
public_bytes = private_key.public_key().public_bytes(
    serialization.Encoding.Raw,
    serialization.PublicFormat.Raw,
)

def write_all(fd, payload):
    view = memoryview(payload)
    while view:
        view = view[os.write(fd, view):]

private_fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
public_fd = os.open(public_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    write_all(private_fd, private_bytes)
    os.fsync(private_fd)
    write_all(public_fd, public_bytes)
    os.fsync(public_fd)
finally:
    os.close(private_fd)
    os.close(public_fd)
PY
chmod 0600 "$COURSE_MODE_OPERATOR_KEY_DIR/course-mode-preflight-ed25519.pem"
```

Review the raw public key and its SHA-256 fingerprint on a separate trusted
channel:

```bash
wc -c "$COURSE_MODE_OPERATOR_KEY_DIR/course-mode-preflight-ed25519.raw.pub"
shasum -a 256 "$COURSE_MODE_OPERATOR_KEY_DIR/course-mode-preflight-ed25519.raw.pub"
xxd -p -c 64 "$COURSE_MODE_OPERATOR_KEY_DIR/course-mode-preflight-ed25519.raw.pub"
```

The public file must be exactly 32 bytes. After independent review, replace only
`PINNED_APPROVAL_PUBLIC_KEY_RAW` and `PINNED_APPROVAL_KEY_FINGERPRINT` in
`scripts/course_mode_physical_tft_preflight.py`, run the focused tests, and
commit only that public pin. Until then, the checked-in `unprovisioned` value is
an intentional fail-closed gate.

## Offline detached signature

Canonicalize the final expected identity, then sign those exact bytes. Python's
`sort_keys=True`, `separators=(",", ":")`, UTF-8 output, and disabled NaN values
match the verifier:

```bash
export COURSE_MODE_IDENTITY=/absolute/reviewed/expected-identity.json
export COURSE_MODE_CANONICAL=/absolute/operator-controlled/expected-identity.canonical.json
export COURSE_MODE_IDENTITY_SIG=/absolute/reviewed/expected-identity.sig
python3 - "$COURSE_MODE_IDENTITY" "$COURSE_MODE_CANONICAL" <<'PY'
import json
import os
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])

def strict_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value

value = json.loads(
    source.read_text(encoding="utf-8"),
    object_pairs_hook=strict_pairs,
    parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
)
canonical = json.dumps(
    value,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8")
fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    view = memoryview(canonical)
    while view:
        view = view[os.write(fd, view):]
    os.fsync(fd)
finally:
    os.close(fd)
PY
python3 - \
  "$COURSE_MODE_OPERATOR_KEY_DIR/course-mode-preflight-ed25519.pem" \
  "$COURSE_MODE_CANONICAL" \
  "$COURSE_MODE_IDENTITY_SIG" <<'PY'
import os
import pathlib
import sys

from cryptography.hazmat.primitives import serialization

private_key = serialization.load_pem_private_key(pathlib.Path(sys.argv[1]).read_bytes(), password=None)
canonical = pathlib.Path(sys.argv[2]).read_bytes()
signature = private_key.sign(canonical)
if len(signature) != 64:
    raise SystemExit("unexpected Ed25519 signature length")
fd = os.open(sys.argv[3], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    view = memoryview(signature)
    while view:
        view = view[os.write(fd, view):]
    os.fsync(fd)
finally:
    os.close(fd)
PY
test "$(wc -c < "$COURSE_MODE_IDENTITY_SIG" | tr -d ' ')" = 64
```

The detached signature must be a 64-byte file. Delete the temporary canonical
copy after verification; retain the reviewed identity, signature, raw public
key, and public fingerprint according to the evidence policy. Keep the private
key offline with mode `0600`.
