import warnings


warnings.filterwarnings(
    "ignore",
    message=r"urllib3 v2 only supports OpenSSL 1\.1\.1\+.*",
    category=Warning,
)
warnings.filterwarnings(
    "ignore",
    message=r"'audioop' is deprecated and slated for removal in Python 3\.13",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r"Using `@model_validator` with mode='after' on a classmethod is deprecated.*",
    category=DeprecationWarning,
)
