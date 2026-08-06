"""Loader + tamper anchor for the shared backend <-> ESP golden vectors (T5.1).

The vectors file is VENDORED byte-for-byte from tbot-backend
(``contracts/lesson-cache-key.vectors.json``). The SAME sha256 is frozen in the
backend at ``src/lessons/lesson-contract-vectors.ts``. Each repo's default test
suite asserts its own copy against this constant, so an edit to either copy that
is not mirrored into the other — and into BOTH constants — turns that pipeline
RED. This is deliberately a hard assertion, not a warning: an advisory sync
check is the exact failure mode T5.1 exists to remove (stale vectors give false
confidence).

To change the vectors, work in tbot-backend:
    node scripts/generate-lesson-contract-vectors.mjs      # prints the new sha
    node scripts/sync-lesson-contract-vectors.mjs          # re-vendors here
then paste the sha into BOTH frozen constants.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

LESSON_CONTRACT_VECTORS_SHA256 = (
    "95e35b3576656c08562d58ac818870fbc4620c9b9ebc1a418fe81a8c861a4ddb"
)

LESSON_CONTRACT_VECTORS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "contracts",
    "lesson-cache-key.vectors.json",
)


class ContractVectorsDrift(RuntimeError):
    """The vendored vectors are not the frozen bytes both repos agreed on."""


def load_lesson_contract_vectors() -> dict[str, Any]:
    with open(LESSON_CONTRACT_VECTORS_PATH, "rb") as handle:
        raw = handle.read()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != LESSON_CONTRACT_VECTORS_SHA256:
        raise ContractVectorsDrift(
            "lesson contract vectors drifted: expected sha256 "
            f"{LESSON_CONTRACT_VECTORS_SHA256}, read {digest}. Regenerate in "
            "tbot-backend, update the frozen constant in BOTH repos, and re-vendor."
        )
    return json.loads(raw.decode("utf-8"))
