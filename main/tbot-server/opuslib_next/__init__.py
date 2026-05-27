import ctypes.util
import importlib.machinery
import os
import sys

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


def _load_real_package():
    ctypes.util.find_library = _find_library_with_opus_fallback
    shim_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    search_path = [
        path
        for path in sys.path
        if path and os.path.abspath(path) != shim_parent
    ]
    spec = importlib.machinery.PathFinder.find_spec(__name__, search_path)
    if spec is None or spec.origin is None or spec.submodule_search_locations is None:
        raise ImportError("Could not locate installed opuslib_next package")

    globals()["__file__"] = spec.origin
    globals()["__path__"] = list(spec.submodule_search_locations)
    globals()["__package__"] = __name__
    with open(spec.origin, "r", encoding="utf-8") as source_file:
        source = source_file.read()
    exec(compile(source, spec.origin, "exec"), globals(), globals())


_load_real_package()
