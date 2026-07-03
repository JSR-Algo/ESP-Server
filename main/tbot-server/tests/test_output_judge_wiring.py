"""Wiring test: GoogleLiveProvider._build_output_judge.

Verifies the judge is built from conn.llm (async-adapted, off-loop) and disabled
when no usable LLM is present — without constructing the heavy provider.
"""

import asyncio
import types
import unittest

from core.voice.session_provider.google_live import GoogleLiveProvider


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeLLM:
    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    def response_no_stream(self, system_prompt, user_prompt, **_kw):
        self.calls.append((system_prompt, user_prompt))
        return self._reply


def _provider_with_llm(llm):
    # Bind the unbound method to a light stand-in carrying only .conn.llm.
    stub = types.SimpleNamespace(conn=types.SimpleNamespace(llm=llm))
    return types.SimpleNamespace(
        _build_output_judge=lambda: GoogleLiveProvider._build_output_judge(stub)
    )


class BuildOutputJudgeTest(unittest.TestCase):
    def test_no_llm_returns_none(self):
        self.assertIsNone(_provider_with_llm(None)._build_output_judge())

    def test_llm_without_response_no_stream_returns_none(self):
        self.assertIsNone(_provider_with_llm(object())._build_output_judge())

    def test_unsafe_verdict_flags(self):
        llm = _FakeLLM("UNSAFE")
        judge = _provider_with_llm(llm)._build_output_judge()
        self.assertIsNotNone(judge)
        self.assertTrue(run(judge("some subtle unhealthy content")))
        self.assertEqual(len(llm.calls), 1)

    def test_safe_verdict_does_not_flag(self):
        judge = _provider_with_llm(_FakeLLM("SAFE"))._build_output_judge()
        self.assertFalse(run(judge("let's practice: apple")))

    def test_llm_exception_fails_open(self):
        class Boom:
            def response_no_stream(self, *_a, **_k):
                raise RuntimeError("provider down")

        judge = _provider_with_llm(Boom())._build_output_judge()
        self.assertFalse(run(judge("hello")))


if __name__ == "__main__":
    unittest.main()
