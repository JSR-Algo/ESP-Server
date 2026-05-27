from enum import Enum
from typing import Union, Optional


class InterfaceType(Enum):
    # API type
    STREAM = "STREAM"  # Streaming API
    NON_STREAM = "NON_STREAM"  # Non-streaming API
    LOCAL = "LOCAL"  # Local Service
