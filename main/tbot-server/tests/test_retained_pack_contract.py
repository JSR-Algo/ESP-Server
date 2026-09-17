import hashlib
import json
from pathlib import Path

import pytest

from core.lesson.retained_pack_contract import parse_operation, parse_receipt

VECTOR_FILE = Path(__file__).resolve().parents[1] / 'contracts/retained-assignment-pack.v1.vectors.json'
VECTORS = json.loads(VECTOR_FILE.read_text())


def test_vectors_pin():
    assert hashlib.sha256(VECTOR_FILE.read_bytes()).hexdigest() == '73db26689cb52f78b50ae5eab055cd1fc9d6077e602e16e6170d13fa037004a4'


@pytest.mark.parametrize('vector', VECTORS['valid'], ids=lambda v: v['name'])
def test_valid_operations(vector):
    assert parse_operation(vector['operation']) == vector['operation']


@pytest.mark.parametrize('vector', VECTORS['invalid'], ids=lambda v: v['name'])
def test_invalid_operations(vector):
    with pytest.raises(ValueError):
        parse_operation(vector['operation'])


def test_receipt_requires_exact_operation_and_no_ready_claim():
    operation = parse_operation(VECTORS['valid'][0]['operation'])
    receipt = {key: operation[key] for key in ['contractVersion', 'operationId', 'requestId',
        'requestRevision', 'deviceId', 'consumerIdentity', 'desiredSelectionRevision']}
    receipt.update({key: operation['selection'][key] for key in ['cacheKey', 'packDescriptorChecksum']})
    receipt['state'] = 'materialized'
    assert parse_receipt(receipt, operation) == receipt
    for delta in [{'state': 'ready'}, {'requestRevision': True}, {'deviceReady': True},
                  {'consumerIdentity': operation['deviceId']}]:
        with pytest.raises(ValueError):
            parse_receipt({**receipt, **delta}, operation)
