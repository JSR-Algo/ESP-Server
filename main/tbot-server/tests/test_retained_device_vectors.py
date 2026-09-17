import json
from pathlib import Path
import pytest
from core.lesson.retained_pack_contract import parse_device_receipt, parse_operation

VECTORS = json.loads((Path(__file__).resolve().parents[1] /
    'contracts/retained-assignment-device.v1.vectors.json').read_text())

@pytest.mark.parametrize('row', VECTORS['valid'], ids=lambda row: row['name'])
def test_exact_device_receipt(row):
    assert parse_device_receipt(row['receipt'], parse_operation(row['operation'])) == row['receipt']

@pytest.mark.parametrize('row', VECTORS['invalid'], ids=lambda row: row['name'])
def test_mismatched_device_receipt(row):
    with pytest.raises(ValueError):
        parse_device_receipt(row['receipt'], parse_operation(row['operation']))
