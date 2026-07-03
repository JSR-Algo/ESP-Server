"""Optional LLM-judge layer for child-safety output moderation.

The fast regex screen (`child_safety.screen_model_output`) is the always-on first
line and stays authoritative for the categories it covers. This judge is a SECOND
line for subtle unsafe content that a keyword denylist cannot catch (tone, implied
meaning, "unhealthy"/khong-lanh-manh phrasing). It is:

- opt-in (only runs when an llm callable is supplied),
- bounded by a strict timeout so it never stalls the realtime voice path,
- fail-OPEN on any error/timeout (never blocks safe speech on an infra hiccup),
- escalate-to-BLOCK only on a clear "unsafe" verdict.

It does NOT replace the regex screen; callers run regex first (instant block) and
only consult the judge when regex passed, to catch what regex missed.
"""

import asyncio
import re

# Kept short: this rides the realtime turn. A judge slower than this is worse than
# no judge (it delays child audio), so we time out and fail open.
DEFAULT_JUDGE_TIMEOUT_S = 1.2

JUDGE_SYSTEM = (
    "You are a strict child-safety classifier for a Vietnamese children's English "
    "tutor robot (ages 4-8). Decide if the ASSISTANT text below is safe to speak to "
    "a young child. Unsafe = profanity, insults, violence, sexual/adult content, "
    "drugs/alcohol/gambling, hate, self-harm, dangerous instructions, scary or "
    "'unhealthy' (khong lanh manh) content, PII solicitation, or grooming. "
    "Reply with EXACTLY one word: SAFE or UNSAFE."
)

_UNSAFE_RE = re.compile(r"\bUNSAFE\b", re.I)
_SAFE_RE = re.compile(r"\bSAFE\b", re.I)


def _verdict_from_text(text):
    """Parse a judge reply into True(unsafe)/False(safe)/None(unparseable)."""
    if text is None:
        return None
    s = str(text)
    # Check UNSAFE first: "UNSAFE" contains "SAFE" as a substring, but \bSAFE\b
    # won't match inside UNSAFE due to the word boundary — still, order defensively.
    if _UNSAFE_RE.search(s):
        return True
    if _SAFE_RE.search(s):
        return False
    return None


async def judge_output_unsafe(text, llm_call, *, timeout_s=DEFAULT_JUDGE_TIMEOUT_S):
    """Return True if the judge is confident the text is UNSAFE, else False.

    `llm_call` is an async callable `llm_call(system, user) -> str` returning the
    judge's raw reply. Any exception, timeout, or unparseable reply => fail OPEN
    (return False) so the realtime path is never blocked by judge failure.
    """
    candidate = "" if text is None else str(text).strip()
    if not candidate or llm_call is None:
        return False
    try:
        reply = await asyncio.wait_for(llm_call(JUDGE_SYSTEM, candidate), timeout=timeout_s)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - fail open by design
        return False
    return _verdict_from_text(reply) is True
