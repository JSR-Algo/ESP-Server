from __future__ import annotations

from core.lesson.word_mastery import EvidenceLevel, WordMastery


def eligible(mastery: WordMastery, *, now_ms: int = 30_000) -> None:
    mastery.record_model(now_ms=1_000)
    mastery.record_intervening_activity()
    mastery.set_target_text_visible(False)
    mastery.set_robot_audio_contaminated(False)
    assert mastery.answer_leakage.independent_eligible(now_ms)


def test_immediate_repetition_is_supported_not_independent() -> None:
    mastery = WordMastery(target_id="animals.cat")
    mastery.record_model(now_ms=1_000)
    result = mastery.record_speech(
        evidence_id="e1", activity_id="repeat", context_id="first", now_ms=5_000,
        semantic_class="target_en", speech_class="exact", assessment_eligible=True,
        confidence_band="high",
    )
    assert result.level is EvidenceLevel.SUPPORTED_SPEECH


def test_known_word_before_any_model_can_be_independent_recall() -> None:
    mastery = WordMastery(target_id="animals.cat")
    result = mastery.record_speech(
        evidence_id="early", activity_id="recall", context_id="visual", now_ms=2_000,
        semantic_class="target_en", speech_class="exact", assessment_eligible=True,
        confidence_band="high",
    )
    assert result.level is EvidenceLevel.INDEPENDENT_RECALL


def test_mastery_requires_meaning_independent_transfer_delayed_and_confidence() -> None:
    mastery = WordMastery(target_id="animals.cat")
    mastery.record_meaning(evidence_id="m", activity_id="meaning", context_id="choice")
    eligible(mastery)
    mastery.record_speech(
        evidence_id="r", activity_id="recall", context_id="visual", now_ms=30_000,
        semantic_class="target_en", speech_class="exact", assessment_eligible=True,
        confidence_band="high",
    )
    mastery.record_transfer(evidence_id="t", activity_id="transfer", context_id="scene")
    result = mastery.record_delayed_recall(
        evidence_id="d", activity_id="delayed", context_id="callback", now_ms=70_000,
        assessment_eligible=True, confidence_band="high",
    )
    assert result.level is EvidenceLevel.MASTERED_TODAY


def test_delayed_recall_requires_an_intervening_activity_without_prior_model() -> None:
    mastery = WordMastery(target_id="animals.cat")
    mastery.record_meaning(evidence_id="m", activity_id="meaning", context_id="choice")
    mastery.record_speech(
        evidence_id="r", activity_id="recall", context_id="visual", now_ms=1_000,
        semantic_class="target_en", speech_class="exact", assessment_eligible=True,
        confidence_band="high",
    )
    mastery.record_transfer(evidence_id="t", activity_id="transfer", context_id="scene")

    result = mastery.record_delayed_recall(
        evidence_id="d", activity_id="delayed", context_id="callback", now_ms=1_001,
        assessment_eligible=True, confidence_band="high",
    )

    assert result.level is EvidenceLevel.TRANSFERRED
    assert result.review_needed is True


def test_ineligible_low_confidence_contaminated_visible_duplicate_or_stale_never_advances() -> None:
    mastery = WordMastery(target_id="animals.cat")
    mastery.record_model(now_ms=1_000)
    mastery.record_intervening_activity()
    mastery.set_target_text_visible(True)
    first = mastery.record_speech(
        evidence_id="same", activity_id="recall", context_id="visual", now_ms=30_000,
        semantic_class="target_en", speech_class="exact", assessment_eligible=True,
        confidence_band="low",
    )
    duplicate = mastery.record_speech(
        evidence_id="same", activity_id="recall", context_id="visual", now_ms=31_000,
        semantic_class="target_en", speech_class="exact", assessment_eligible=True,
        confidence_band="high",
    )
    assert first.level is EvidenceLevel.SUPPORTED_SPEECH
    assert duplicate.accepted is False
    assert mastery.level is EvidenceLevel.SUPPORTED_SPEECH


def test_later_miss_preserves_prior_evidence_and_can_recommend_review() -> None:
    mastery = WordMastery(target_id="animals.cat")
    mastery.record_meaning(evidence_id="m", activity_id="meaning", context_id="choice")
    eligible(mastery)
    mastery.record_speech(
        evidence_id="r", activity_id="recall", context_id="visual", now_ms=30_000,
        semantic_class="target_en", speech_class="exact", assessment_eligible=True,
        confidence_band="high",
    )
    result = mastery.record_speech(
        evidence_id="miss", activity_id="recall2", context_id="visual2", now_ms=50_000,
        semantic_class="unknown", speech_class="silence", assessment_eligible=True,
        confidence_band="high",
    )
    assert result.level is EvidenceLevel.INDEPENDENT_RECALL
    assert mastery.recommend_review().review_needed is True


def test_snapshot_restore_preserves_consumed_evidence_without_raw_child_content() -> None:
    mastery = WordMastery(target_id="animals.cat")
    mastery.record_meaning(evidence_id="m", activity_id="meaning", context_id="choice")
    snapshot = mastery.snapshot()
    assert "transcript" not in repr(snapshot).lower()
    restored = WordMastery.restore(snapshot)
    replay = restored.record_meaning(evidence_id="m", activity_id="meaning", context_id="choice")
    assert replay.accepted is False
    assert restored.level is EvidenceLevel.UNDERSTOOD


def test_later_transfer_cannot_downgrade_mastered_today() -> None:
    mastery = WordMastery(target_id="animals.cat")
    mastery.record_meaning(evidence_id="m", activity_id="meaning", context_id="choice")
    eligible(mastery)
    mastery.record_speech(
        evidence_id="r", activity_id="recall", context_id="visual", now_ms=30_000,
        semantic_class="target_en", speech_class="exact", assessment_eligible=True,
        confidence_band="high",
    )
    mastery.record_transfer(evidence_id="t", activity_id="transfer", context_id="scene")
    mastery.record_delayed_recall(
        evidence_id="d", activity_id="delayed", context_id="callback", now_ms=70_000,
        assessment_eligible=True, confidence_band="high",
    )

    result = mastery.record_transfer(
        evidence_id="later-transfer", activity_id="transfer", context_id="scene",
    )

    assert result.level is EvidenceLevel.MASTERED_TODAY


def test_later_recall_cannot_downgrade_mastered_today() -> None:
    mastery = WordMastery(target_id="animals.cat")
    mastery.record_meaning(evidence_id="m", activity_id="meaning", context_id="choice")
    eligible(mastery)
    mastery.record_speech(
        evidence_id="r", activity_id="recall", context_id="visual", now_ms=30_000,
        semantic_class="target_en", speech_class="exact", assessment_eligible=True,
        confidence_band="high",
    )
    mastery.record_transfer(evidence_id="t", activity_id="transfer", context_id="scene")
    mastery.record_delayed_recall(
        evidence_id="d", activity_id="delayed", context_id="callback", now_ms=70_000,
        assessment_eligible=True, confidence_band="high",
    )

    result = mastery.record_speech(
        evidence_id="later-recall", activity_id="recall", context_id="visual",
        now_ms=100_000, semantic_class="target_en", speech_class="exact",
        assessment_eligible=True, confidence_band="high",
    )

    assert result.level is EvidenceLevel.MASTERED_TODAY
