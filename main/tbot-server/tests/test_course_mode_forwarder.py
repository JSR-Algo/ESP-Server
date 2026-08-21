from __future__ import annotations

import json

import pytest

from core.lesson.forwarder import serialize_word_evidence_event


def test_word_evidence_event_has_exact_privacy_safe_fields() -> None:
    event = serialize_word_evidence_event({
        "sequence": 12, "targetId": "animals.cat", "evidenceLevel": "INDEPENDENT_RECALL",
        "activityId": "cat-recall-02", "contextId": "second-visual",
        "supportCodesSinceLastModel": [], "elapsedSinceFullModelMs": 32_000,
        "interveningActivityCount": 1, "assessmentConfidenceBand": "high", "reviewNeeded": False,
    })
    assert set(event) == {
        "type", "sequence", "targetId", "evidenceLevel", "activityId", "contextId",
        "supportCodesSinceLastModel", "elapsedSinceFullModelMs", "interveningActivityCount",
        "assessmentConfidenceBand", "reviewNeeded",
    }
    serialized = json.dumps(event).casefold()
    for forbidden in ("transcript", "utterance", "audio", "pronunciation", "score", "story"):
        assert forbidden not in serialized


@pytest.mark.parametrize("field", ["transcript", "audio", "childStory", "confidence"])
def test_private_or_free_form_fields_are_rejected(field: str) -> None:
    with pytest.raises(ValueError):
        serialize_word_evidence_event({
            "sequence": 1, "targetId": "animals.cat", "evidenceLevel": "EXPOSED",
            "activityId": "a", "contextId": "c", "supportCodesSinceLastModel": [],
            "elapsedSinceFullModelMs": 0, "interveningActivityCount": 0,
            "assessmentConfidenceBand": "high", "reviewNeeded": False, field: "private",
        })

