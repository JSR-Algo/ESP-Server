from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "x-auth-token",
    "x-mint-secret",
    "cookie",
    "set-cookie",
}


class CompatibilityHeaders(dict):
    def __init__(self, pairs: Iterable[tuple[Any, Any]]):
        self._pairs = tuple((str(name), str(value)) for name, value in pairs)
        collapsed: dict[str, tuple[str, str]] = {}
        for name, value in self._pairs:
            collapsed[name.casefold()] = (name, value)
        super().__init__(collapsed.values())

    def raw_items(self) -> Iterator[tuple[str, str]]:
        return iter(self._pairs)

    def get_all(self, name: str) -> list[str]:
        folded = str(name).casefold()
        return [value for key, value in self._pairs if key.casefold() == folded]

    def getall(self, name: str, default=None):
        values = self.get_all(name)
        return values if values else ([] if default is None else default)

    def get(self, name: str, default=None):
        values = self.get_all(name)
        return values[-1] if values else default

def preserve_request_headers(headers: Any) -> CompatibilityHeaders:
    raw_items = getattr(headers, "raw_items", None)
    if callable(raw_items):
        return CompatibilityHeaders(raw_items())
    items = getattr(headers, "items", None)
    return CompatibilityHeaders(items() if callable(items) else ())


def single_header(headers: CompatibilityHeaders, name: str, default=None):
    values = headers.get_all(name)
    if len(values) > 1:
        raise ValueError(f"duplicate {name} header")
    return values[0] if values else default


def sanitize_headers_for_log(headers: Any) -> dict[str, Any]:
    items = getattr(headers, "items", None)
    if not callable(items):
        return {}
    sanitized = {}
    for key, value in items():
        name = str(key)
        sanitized[name] = "<redacted>" if name.casefold() in _SENSITIVE_HEADER_NAMES else value
    return sanitized
