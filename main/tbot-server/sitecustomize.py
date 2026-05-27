import ctypes.util
import os

_ORIGINAL_FIND_LIBRARY = ctypes.util.find_library
_OPUS_CANDIDATE_PATHS = (
    os.environ.get("OPUS_LIB_PATH", ""),
    "/opt/homebrew/opt/opus/lib/libopus.dylib",
    "/usr/local/opt/opus/lib/libopus.dylib",
    "/usr/local/lib/libopus.dylib",
)


def _find_library_with_opus_fallback(name):
    result = _ORIGINAL_FIND_LIBRARY(name)
    if result is not None:
        return result
    if name != "opus":
        return None

    for candidate in _OPUS_CANDIDATE_PATHS:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


ctypes.util.find_library = _find_library_with_opus_fallback
