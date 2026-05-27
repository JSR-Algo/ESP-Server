from enum import Enum
from typing import Union, Optional


class SentenceType(Enum):
    # Speaking Stage
    FIRST = "FIRST"  # First sentence
    MIDDLE = "MIDDLE"  # Speaking
    LAST = "LAST"  # Last Sentence


class ContentType(Enum):
    # Content Type
    TEXT = "TEXT"  # Text Content
    FILE = "FILE"  # File Content
    ACTION = "ACTION"  # Action Content


class InterfaceType(Enum):
    # API type
    DUAL_STREAM = "DUAL_STREAM"  # Dual streaming
    SINGLE_STREAM = "SINGLE_STREAM"  # Single streaming
    NON_STREAM = "NON_STREAM"  # Non-streaming


class TTSMessageDTO:
    def __init__(
        self,
        sentence_id: str,
        # Speaking Stage
        sentence_type: SentenceType,
        # Content Type
        content_type: ContentType,
        # Content details, usually text needing conversion or audio lyrics
        content_detail: Optional[str] = None,
        # If content type is file, file path must be passed
        content_file: Optional[str] = None,
    ):
        self.sentence_id = sentence_id
        self.sentence_type = sentence_type
        self.content_type = content_type
        self.content_detail = content_detail
        self.content_file = content_file
