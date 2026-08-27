"""Bounded, child-safe interaction templates for authored lessons."""

from dataclasses import dataclass
from typing import Dict


FUN_PATTERN_PROMPTS: Dict[str, str] = {
    "mysteryReveal": "A mystery picture is opening. Say {word} when you spot it!",
    "copyMyMove": "Copy TeeBot's move, then say {word}!",
    "sillyChoice": "Silly choice time: point, smile, and say {word}!",
    "whisperThenLoud": "Whisper {word}, then say it with a bright voice!",
    "soundGuess": "Listen to the clue and guess: {word}!",
    "missingObject": "Something is missing. Help us find {word}!",
    "robotForgot": "TeeBot forgot the word. Can you say {word}?",
    "miniStoryRescue": "The pet needs your voice. Say {word} to help!",
    "fastSlow": "Say {word} slowly, then try it quickly!",
    "celebrationFinale": "Finale time! Say {word} and celebrate!",
}


@dataclass(frozen=True)
class SafeSpeakingDecision:
    outcome: str
    result: str
    prompt: str
    motion_slot: str
    advance: bool


class SafeSpeakingSession:
    """A per-step state machine with a hard three-attempt ceiling."""

    def __init__(self, *, max_attempts: int, target_word: str) -> None:
        try:
            requested = int(max_attempts)
        except (TypeError, ValueError, OverflowError):
            requested = 3
        self.max_attempts = max(1, min(3, requested))
        self.target_word = str(target_word or "the word").strip() or "the word"
        self.attempts = 0

    def decide(self, branch: str) -> SafeSpeakingDecision:
        if branch == "correct":
            return SafeSpeakingDecision("correct", "success", f"You found it! {self.target_word}!", "correct", True)
        if branch == "brave_try":
            return SafeSpeakingDecision(
                "brave_try", "miss", f"Brave try! Let's keep {self.target_word} for our story.", "nearMiss", True
            )
        if branch == "supported":
            return SafeSpeakingDecision(
                "supported", "success", f"Yes, in English we say {self.target_word}. Let's continue!", "correct", True
            )

        self.attempts += 1
        if self.attempts >= self.max_attempts:
            return SafeSpeakingDecision(
                "modeled",
                "timeout" if branch in {"silence", "stt_failure"} else "miss",
                f"Great teamwork. TeeBot will model it: {self.target_word}. Let's continue!",
                "nearMiss",
                True,
            )
        prompts = {
            "incorrect": f"Nice try. Listen once more: {self.target_word}.",
            "silence": f"Take your time. When you're ready, try {self.target_word}.",
            "help_or_repeat": f"Of course. Listen and copy: {self.target_word}.",
            "vietnamese_object": f"Yes, that's the object. In English we say {self.target_word}.",
            "stt_failure": f"The robot's ears missed that. Please try {self.target_word} once more.",
        }
        return SafeSpeakingDecision(
            branch if branch in prompts else "incorrect",
            "miss",
            prompts.get(branch, prompts["incorrect"]),
            "incorrect",
            False,
        )


def fun_pattern_prompt(pattern: str, target_word: str) -> str:
    template = FUN_PATTERN_PROMPTS.get(str(pattern or ""), FUN_PATTERN_PROMPTS["copyMyMove"])
    return template.format(word=str(target_word or "the word").strip() or "the word")


def curriculum_outcome_name(
    *,
    semantic_class: str,
    speech_class: str,
    language: str,
    intent: str,
) -> str:
    """Map child input to an authored curriculum outcome without inventing content."""
    if intent in {"fatigue", "refusal", "help", "silence", "story"}:
        return intent
    if semantic_class == "meaning_vi" or language == "vi":
        return "vietnamese"
    if speech_class == "near":
        return "near"
    if semantic_class == "target_en" and speech_class == "exact":
        return "correct"
    if semantic_class == "silence" or speech_class == "silence":
        return "silence"
    return "incorrect"
