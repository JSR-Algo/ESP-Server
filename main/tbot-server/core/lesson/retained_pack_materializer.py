"""Internal retained preparation; never activates a device or claims device READY."""

from __future__ import annotations

from core.lesson.retained_pack_contract import descriptor, parse_operation
from core.lesson.retained_pack_store import RetainedPackStore
from core.lesson.sd_pack_materializer import (
    MaterializationError, _shared_store, _validate_manifest, materialize_lesson_sd_pack,
)


async def materialize_retained_operation(raw, *, config, client=None, logger=None, resolver=None,
                                         bind_device=None):
    op = parse_operation(raw)
    pack = descriptor(op) if op["action"] == "acquire" else None
    if pack is not None:
        _validate_manifest(pack, config)
    retained = RetainedPackStore(_shared_store(config))
    with retained.operation(op) as work:
        if op["action"] == "acquire":
            work.acquire()
            await materialize_lesson_sd_pack(pack, config=config, client=client, logger=logger, resolver=resolver)
            return work.materialized()
        if op["action"] == "release":
            return work.release()
        if op["action"] == "reconcile":
            return work.reconcile()
        if bind_device is not None:
            # Persist unknown device work before any send. Cancellation and lost
            # replies leave this owner protected across process/connection loss.
            work.begin_bind()
            return work.bound(await bind_device(op))
        raise MaterializationError("RETAINED_DEVICE_CAPABILITY_UNAVAILABLE", 409, False,
                                   "Retained device selection fencing is not available")
