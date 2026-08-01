import os

GOOGLE_LIVE_CREDENTIAL_ENV_NAMES = (
    "GOOGLE_API_KEY",
    "TBOT_GOOGLE_LIVE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_GEMINI_API_KEY",
)


def normalize_google_live_api_key(value):
    return "".join(str(value or "").split())


def resolve_google_live_env_api_key(environ=None):
    source = os.environ if environ is None else environ
    for env_name in GOOGLE_LIVE_CREDENTIAL_ENV_NAMES:
        candidate = normalize_google_live_api_key(source.get(env_name, ""))
        if candidate:
            return candidate
    return ""


def resolve_google_live_api_key(value=None, environ=None):
    source = os.environ if environ is None else environ
    api_key = value or ""
    if isinstance(api_key, str) and api_key.startswith("${") and api_key.endswith("}"):
        env_name = api_key[2:-1]
        if ":-" in env_name:
            env_name = env_name.split(":-", 1)[0]
        api_key = source.get(env_name, "")
    api_key = normalize_google_live_api_key(api_key)
    if api_key:
        return api_key
    return resolve_google_live_env_api_key(source)
