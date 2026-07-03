"""Round-1 branch coverage for ``core.providers.tools.product_toolset``.

Drives the unexercised fallbacks in ``_configured_function_names`` (selected
profile absent -> ``function_call`` fallback; non-Mapping profile -> ``[]``;
non-iterable / ``str`` functions -> ``[]``), the ADULT_CHILD_DENYLIST short-circuit
in ``_is_child_allowed``, and the duplicate-skip ``continue`` in ``_dedupe``.

Pure-function level: no connection, no network, no event loop. We call the
private helpers directly with crafted config shapes, which is the only way to
reach these defensive branches deterministically.
"""

from core.providers.tools.product_toolset import (
    ADULT_CHILD_DENYLIST,
    _configured_function_names,
    _dedupe,
    _is_child_allowed,
)


class _Obj:
    """Plain object whose ``config`` is intentionally NOT a Mapping is built
    elsewhere; here we just need crafted Mapping configs for the helpers."""


def test_configured_function_names_selected_missing_falls_back_to_function_call():
    """Line 102: ``selected`` resolves to a non-Mapping (None here), so the code
    falls back to ``intent_root.get("function_call")``."""
    config = {
        "Intent": {
            "function_call": {"functions": ["play_music", "stop_music"]},
        },
        "selected_module": {"Intent": "Intent_LLM"},  # no matching profile -> None
    }
    assert _configured_function_names(config) == ["play_music", "stop_music"]


def test_configured_function_names_function_call_also_non_mapping_returns_empty():
    """Line 104: after the function_call fallback, the profile is still not a
    Mapping (it is a list), so the function returns ``[]``."""
    config = {
        "Intent": {
            "function_call": ["not", "a", "mapping"],
        },
    }
    assert _configured_function_names(config) == []


def test_configured_function_names_functions_is_str_returns_empty():
    """Line 108: ``functions`` is a ``str`` (Iterable but explicitly excluded),
    so the function returns ``[]`` rather than iterating characters."""
    config = {
        "Intent": {
            "function_call": {"functions": "play_music"},
        },
    }
    assert _configured_function_names(config) == []


def test_configured_function_names_functions_non_iterable_returns_empty():
    """Line 108 (sibling branch): ``functions`` is a non-iterable int -> ``[]``."""
    config = {
        "Intent": {
            "function_call": {"functions": 123},
        },
    }
    assert _configured_function_names(config) == []


def test_is_child_allowed_denylisted_name_is_false():
    """Line 114: a name in ADULT_CHILD_DENYLIST is rejected before the danger
    regex is ever consulted."""
    denied = sorted(ADULT_CHILD_DENYLIST)[0]
    assert _is_child_allowed(denied) is False
    # Sanity: a benign, non-denied, non-danger name passes.
    assert _is_child_allowed("change_volume") is True


def test_dedupe_skips_repeated_name():
    """Line 123: the duplicate-skip ``continue`` fires for a repeated name while
    preserving first-seen order."""
    assert _dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]
