"""Restart-persistent extra pack ownership, serialized with deletion and acquisition."""

from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from core.lesson.retained_pack_contract import (
    parse_device_receipt, parse_operation, parse_receipt, parse_selection, receipt_for, revision, uuid_value,
)
from core.lesson.shared_asset_store import SharedAssetStore

DIRECTORY = "lesson-retained"


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _load_identity(root: Path) -> str:
    value = json.loads((root / "identity.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"consumerIdentity"}:
        raise ValueError("invalid retained store identity")
    return uuid_value(value["consumerIdentity"])


def _validate_state(value, consumer, device):
    fields = {"consumerIdentity", "deviceId", "requestId", "requestRevision", "watermark",
              "selection", "phase", "operations"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("invalid retained state")
    if value["consumerIdentity"] != consumer or value["deviceId"] != device:
        raise ValueError("retained state identity mismatch")
    uuid_value(value["requestId"])
    revision(value["requestRevision"])
    revision(value["watermark"])
    parse_selection(value["selection"])
    if value["phase"] not in {"ACQUIRE_PENDING", "HELD", "BIND_PENDING", "BOUND", "RELEASE_PENDING", "RELEASED"}:
        raise ValueError("invalid retained phase")
    if not isinstance(value["operations"], dict) or not value["operations"]:
        raise ValueError("missing retained operation history")
    latest = []
    for key, entry in value["operations"].items():
        uuid_value(key)
        if not isinstance(entry, dict) or set(entry) != {"operation", "receipt"}:
            raise ValueError("invalid retained operation record")
        op = parse_operation(entry["operation"])
        if (op["operationId"] != key or op["deviceId"] != device or op["consumerIdentity"] != consumer
                or op["requestId"] != value["requestId"] or op["selection"] != value["selection"]
                or op["desiredSelectionRevision"] > value["watermark"]
                or op["requestRevision"] > value["requestRevision"]):
            raise ValueError("retained operation identity mismatch")
        if entry["receipt"] is not None:
            parse_receipt(entry["receipt"], op)
        if op["desiredSelectionRevision"] == value["watermark"]:
            latest.append(entry)
    if len(latest) != 1 or latest[0]["operation"]["requestRevision"] != value["requestRevision"]:
        raise ValueError("retained watermark has no exact operation")
    receipt = latest[0]["receipt"]
    phase = value["phase"]
    if phase in {"ACQUIRE_PENDING", "BIND_PENDING"}:
        action = latest[0]["operation"]["action"]
        expected_action = "acquire" if phase == "ACQUIRE_PENDING" else "bind"
        if not ((receipt is None and action == expected_action)
                or (action == "reconcile" and receipt is not None and receipt["state"] == "protected")):
            raise ValueError("invalid pending acquisition")
    else:
        permitted = {"HELD": {"materialized", "protected"}, "BOUND": {"bound", "protected"},
                     "RELEASE_PENDING": {"release_pending"}, "RELEASED": {"released"}}
        if receipt is None or receipt["state"] not in permitted[phase]:
            raise ValueError("retained phase has no exact durable receipt")
    return value


def retained_protected_cache_keys(store: SharedAssetStore) -> set[str]:
    """Called under the shared store's GC lock; malformed metadata blocks deletion."""
    root = store.root / DIRECTORY
    if not root.exists():
        return set()
    consumer = _load_identity(root)
    devices = root / "devices"
    keys = set()
    if devices.exists():
        for path in devices.iterdir():
            if path.suffix != ".json":
                continue
            device = uuid_value(path.stem)
            value = _validate_state(json.loads(path.read_text(encoding="utf-8")), consumer, device)
            if value["phase"] != "RELEASED":
                keys.add(value["selection"]["cacheKey"])
    return keys


class RetainedPackStore:
    def __init__(self, store: SharedAssetStore):
        self.store = store
        self.root = store.root / DIRECTORY
        with store._gc_lock(exclusive=True):
            identity = self.root / "identity.json"
            if not identity.exists():
                if self.root.exists() and any(self.root.iterdir()):
                    raise ValueError("retained store identity missing")
                self.root.mkdir(parents=True, exist_ok=True)
                store._fsync_dir(store.root)
                store._atomic_write(identity, _encode({"consumerIdentity": str(uuid4())}))
            self.consumer_identity = _load_identity(self.root)
            for directory in ["devices", "locks"]:
                (self.root / directory).mkdir(exist_ok=True)
            store._fsync_dir(self.root)

    @contextmanager
    def operation(self, raw):
        op = parse_operation(raw)
        if op["consumerIdentity"] != self.consumer_identity:
            raise ValueError("retained consumer identity mismatch")
        locks = self.root / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        # Keep the device lock for the entire asynchronous materialization. A
        # release cannot overtake it; contention is retryable, never a blocking
        # flock on the event loop. Persistent protection survives process death.
        with (locks / (op["deviceId"] + ".lock")).open("a+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("retained operation busy") from None
            work = _RetainedOperation(self, op)
            yield work


class _RetainedOperation:
    def __init__(self, owner: RetainedPackStore, op):
        self.owner = owner
        self.op = op
        self.path = owner.root / "devices" / (op["deviceId"] + ".json")
        try:
            self.state = _validate_state(json.loads(self.path.read_text(encoding="utf-8")),
                                         owner.consumer_identity, op["deviceId"])
        except FileNotFoundError:
            self.state = None
        state = self.state
        if state is not None:
            if op["desiredSelectionRevision"] < state["watermark"]:
                raise ValueError("stale retained selection")
            if op["requestId"] == state["requestId"]:
                if op["selection"] != state["selection"] or op["requestRevision"] < state["requestRevision"]:
                    raise ValueError("retained owner changed")
                prior = state["operations"].get(op["operationId"])
                if prior is not None and prior["operation"] != op:
                    raise ValueError("retained operation body changed")
                if op["desiredSelectionRevision"] == state["watermark"] and prior is None:
                    raise ValueError("retained revision already used")
                if state["phase"] in {"RELEASE_PENDING", "RELEASED"} and op["action"] in {"acquire", "bind"}:
                    raise ValueError("retained owner released")
            elif state["phase"] != "RELEASED" or op["desiredSelectionRevision"] <= state["watermark"]:
                raise ValueError("another retained owner exists")

    def _persist(self, phase, receipt=None):
        op = self.op
        if self.state is None or self.state["requestId"] != op["requestId"]:
            self.state = {"consumerIdentity": op["consumerIdentity"], "deviceId": op["deviceId"],
                          "requestId": op["requestId"], "selection": op["selection"], "operations": {}}
        self.state.update(requestRevision=op["requestRevision"], watermark=op["desiredSelectionRevision"], phase=phase)
        self.state["operations"][op["operationId"]] = {"operation": op, "receipt": receipt}
        _validate_state(self.state, self.owner.consumer_identity, op["deviceId"])
        with self.owner.store._gc_lock(exclusive=False):
            self.owner.store._atomic_write(self.path, _encode(self.state))
        return receipt

    def acquire(self):
        if self.op["action"] != "acquire":
            raise ValueError("acquire operation required")
        if self.state is not None and self.state['phase'] in {'BIND_PENDING', 'BOUND', 'RELEASE_PENDING'}:
            raise ValueError('device owner cannot become preparation')
        self._persist("ACQUIRE_PENDING")

    def materialized(self):
        if self.op["action"] != "acquire" or self.state is None or self.state["phase"] != "ACQUIRE_PENDING":
            raise ValueError("acquisition required before materialization")
        if not self.owner.store.is_pack_ready(self.op["selection"]["cacheKey"]):
            raise ValueError("retained pack is unavailable")
        return self._persist("HELD", receipt_for(self.op, "materialized"))

    def release(self):
        if self.op["action"] != "release":
            raise ValueError("release operation required")
        # No device release proof exists for a bound selection in the selected
        # terminal contract. Only preparation, which never touches a device,
        # can release after this lock has drained its actual materializer.
        if self.state is not None and self.state["phase"] in {"BIND_PENDING", "BOUND", "RELEASE_PENDING"}:
            return self._persist("RELEASE_PENDING", receipt_for(self.op, "release_pending"))
        return self._persist("RELEASED", receipt_for(self.op, "released"))

    def begin_bind(self):
        if (self.op['action'] != 'bind' or self.state is None
                or self.state['phase'] not in {'HELD', 'BIND_PENDING', 'BOUND'}
                or self.state['requestId'] != self.op['requestId']):
            raise ValueError('materialized owner required before binding')
        if not self.owner.store.is_pack_ready(self.op['selection']['cacheKey']):
            raise ValueError('retained pack is unavailable')
        for entry in self.state['operations'].values():
            prior = entry['operation']
            if prior['action'] == 'bind' and any(prior[key] != self.op[key]
                    for key in ('assignmentId', 'assignmentVersion')):
                raise ValueError('retained assignment changed')
        self._persist('BIND_PENDING')

    def bound(self, device_receipt):
        if self.state is None or self.state['phase'] != 'BIND_PENDING':
            raise ValueError('durable bind required before device receipt')
        parse_device_receipt(device_receipt, self.op)
        return self._persist('BOUND', receipt_for(self.op, 'bound'))

    def reconcile(self):
        if self.op["action"] != "reconcile" or self.state is None or self.state["requestId"] != self.op["requestId"]:
            raise ValueError("retained reference is unknown")
        phase = self.state["phase"]
        status = "released" if phase == "RELEASED" else "release_pending" if phase == "RELEASE_PENDING" else "protected"
        return self._persist(phase, receipt_for(self.op, status))
