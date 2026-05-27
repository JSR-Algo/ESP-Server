import re
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

EMOTION_EMOJI_MAP = {
    "HAPPY": "🙂",
    "SAD": "😔",
    "ANGRY": "😡",
    "NEUTRAL": "😶",
    "FEARFUL": "😰",
    "DISGUSTED": "🤢",
    "SURPRISED": "😲",
    "EMO_UNKNOWN": "😶",  # UnknownEmotion defaults to neutral expression
}
# EVENT_EMOJI_MAP = {
#     "<|BGM|>": "🎼",
#     "<|Speech|>": "",
#     "<|Applause|>": "👏",
#     "<|Laughter|>": "😀",
#     "<|Cry|>": "😭",
#     "<|Sneeze|>": "🤧",
#     "<|Breath|>": "",
#     "<|Cough|>": "🤧",
# }

def lang_tag_filter(text: str) -> dict | str:
    """
    Parse FunASR recognition result, extract tags and plain text content in order

    Args:
        text: original ASR recognition text, may contain multiple tags

    Returns:
        dict: {"language": "zh", "emotion": "SAD", "emoji": "😔", "content": "Yougood"} if tags exist
        str: plain text if no tags

    Examples:
        FunASR output format: <|language|><|emotion|><|event|><|other_options|>original text
        >>> lang_tag_filter("<|zh|><|SAD|><|Speech|><|withitn|>YouOkay, test test.")
        {"language": "zh", "emotion": "SAD", "emoji": "😔", "content": "YouOkay, test test."}
        >>> lang_tag_filter("<|en|><|HAPPY|><|Speech|><|withitn|>Hello hello.")
        {"language": "en", "emotion": "HAPPY", "emoji": "🙂", "content": "Hello hello."}
        >>> lang_tag_filter("plain text")
        "plain text"
    """
    # Extract all tags (in order)
    tag_pattern = r"<\|([^|]+)\|>"
    all_tags = re.findall(tag_pattern, text)

    # Remove All <|...|> format tags, get plain text
    clean_text = re.sub(tag_pattern, "", text).strip()

    # If no tags, return plain text directly
    if not all_tags:
        return clean_text

    # According to FunASR Extract labels in fixed order, return dict
    language = all_tags[0] if len(all_tags) > 0 else "zh"
    emotion = all_tags[1] if len(all_tags) > 1 else "NEUTRAL"
    # event = all_tags[2] if len(all_tags) > 2 else "Speech"  # EventTags not used now

    result = {
        "content": clean_text,
        "language": language,
        "emotion": emotion,
        # "event": event,
    }

    # Add emoji Map
    if emotion in EMOTION_EMOJI_MAP:
        result["emotion"] = EMOTION_EMOJI_MAP[emotion]
    # EventTags not used now
    # if event in EVENT_EMOJI_MAP:
    #     result["event"] = EVENT_EMOJI_MAP[event]

    return result

