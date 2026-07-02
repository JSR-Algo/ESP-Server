"""Tests for the optional LLM-judge output moderation layer.

Critical properties:
- escalate-to-block only on a clear UNSAFE verdict,
- fail OPEN on error/timeout/unparseable (never block safe speech on infra fault),
- never runs (returns safe) when no llm is configured.
"""

import asyncio
import unittest

from core.voice.output_safety_judge import judge_output_unsafe, _verdict_from_text


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class VerdictParseTest(unittest.TestCase):
    def test_parses_unsafe_and_safe(self):
        self.assertIs(_verdict_from_text("UNSAFE"), True)
        self.assertIs(_verdict_from_text("unsafe - profanity"), True)
        self.assertIs(_verdict_from_text("SAFE"), False)
        self.assertIs(_verdict_from_text("safe to speak"), False)

    def test_unparseable_is_none(self):
        self.assertIsNone(_verdict_from_text("maybe?"))
        self.assertIsNone(_verdict_from_text(""))
        self.assertIsNone(_verdict_from_text(None))


class JudgeTest(unittest.TestCase):
    def test_no_llm_configured_never_blocks(self):
        self.assertFalse(run(judge_output_unsafe("anything", None)))

    def test_empty_text_never_blocks(self):
        async def llm(_s, _u):
            raise AssertionError("should not be called for empty text")
        self.assertFalse(run(judge_output_unsafe("", llm)))

    def test_clear_unsafe_verdict_blocks(self):
        async def llm(_s, _u):
            return "UNSAFE"
        self.assertTrue(run(judge_output_unsafe("some subtle unhealthy thing", llm)))

    def test_safe_verdict_does_not_block(self):
        async def llm(_s, _u):
            return "SAFE"
        self.assertFalse(run(judge_output_unsafe("let's practice: apple", llm)))

    def test_llm_exception_fails_open(self):
        async def llm(_s, _u):
            raise RuntimeError("provider down")
        # Must NOT block safe speech just because the judge errored.
        self.assertFalse(run(judge_output_unsafe("hello", llm)))

    def test_timeout_fails_open(self):
        async def slow(_s, _u):
            await asyncio.sleep(5)
            return "UNSAFE"
        self.assertFalse(run(judge_output_unsafe("hello", slow, timeout_s=0.05)))

    def test_unparseable_reply_fails_open(self):
        async def llm(_s, _u):
            return "I think maybe"
        self.assertFalse(run(judge_output_unsafe("hello", llm)))


if __name__ == "__main__":
    unittest.main()
