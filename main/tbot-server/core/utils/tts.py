import os
import re
import sys
import importlib

from config.logger import setup_logging
from core.utils.textUtils import check_emoji

logger = setup_logging()

punctuation_set = {
    ",",
    ",",  # ChineseComma + English Comma
    ".",
    ".",  # ChinesePeriod + English Period
    "!",
    "!",  # ChineseExclamation mark + English exclamation mark
    "“",
    "”",
    '"',  # ChineseDouble quote + English Quotes
    ":",
    ":",  # ChineseColon + English Colon
    "-",
    "-",  # English hyphen + ChineseFull-width Dash
    ",",  # ChineseEnumeration comma
    "[",
    "]",  # Square brackets
    "[",
    "]",  # ChineseSquare brackets
    "~",  # Tilde
}

def create_instance(class_name, *args, **kwargs):
    # CreateTTSInstance
    provider_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "providers", "tts"))
    if os.path.exists(os.path.join(provider_dir, f'{class_name}.py')):
        lib_name = f'core.providers.tts.{class_name}'
        if lib_name not in sys.modules:
            sys.modules[lib_name] = importlib.import_module(f'{lib_name}')
        return sys.modules[lib_name].TTSProvider(*args, **kwargs)

    raise ValueError(f"Unsupported TTS type: {class_name}, check whether type in this config is set correctly")


class MarkdownCleaner:
    """
    Wrap Markdown cleanup logic: use MarkdownCleaner.clean_markdown(text) directly
    """
    # Formula Character
    NORMAL_FORMULA_CHARS = re.compile(r'[a-zA-Z\\^_{}\+\-\(\)\[\]=]')

    @staticmethod
    def _replace_inline_dollar(m: re.Match) -> str:
        """
        Once complete "$...$" is captured:
          - If inside has typical formula characters => remove both-side $
          - Otherwise (pure numbers/currency, etc.) => keep "$...$"
        """
        content = m.group(1)
        if MarkdownCleaner.NORMAL_FORMULA_CHARS.search(content):
            return content
        else:
            return m.group(0)

    @staticmethod
    def _replace_table_block(match: re.Match) -> str:
        """
        Callback this function when full table block is matched.
        """
        block_text = match.group('table_block')
        lines = block_text.strip('\n').split('\n')

        parsed_table = []
        for line in lines:
            line_stripped = line.strip()
            if re.match(r'^\|\s*[-:]+\s*(\|\s*[-:]+\s*)+\|?$', line_stripped):
                continue
            columns = [col.strip() for col in line_stripped.split('|') if col.strip() != '']
            if columns:
                parsed_table.append(columns)

        if not parsed_table:
            return ""

        headers = parsed_table[0]
        data_rows = parsed_table[1:] if len(parsed_table) > 1 else []

        lines_for_tts = []
        if len(parsed_table) == 1:
            # Only One Line
            only_line_str = ", ".join(parsed_table[0])
            lines_for_tts.append(f"Single-line table: {only_line_str}")
        else:
            lines_for_tts.append(f"Header is:{', '.join(headers)}")
            for i, row in enumerate(data_rows, start=1):
                row_str_list = []
                for col_index, cell_val in enumerate(row):
                    if col_index < len(headers):
                        row_str_list.append(f"{headers[col_index]} = {cell_val}")
                    else:
                        row_str_list.append(cell_val)
                lines_for_tts.append(f"No. {i} line:{', '.join(row_str_list)}")

        return "\n".join(lines_for_tts) + "\n"

    # Precompile all regex expressions (by execution frequencySort)
    # Need Put Here replace_xxx static methods placed at front for definition, so they can be correctly referenced in list.
    REGEXES = [
        (re.compile(r'```.*?```', re.DOTALL), ''),  # Code block
        (re.compile(r'^#+\s*', re.MULTILINE), ''),  # Title
        (re.compile(r'(\*\*|__)(.*?)\1'), r'\2'),  # Bold
        (re.compile(r'(\*|_)(?=\S)(.*?)(?<=\S)\1'), r'\2'),  # Italic
        (re.compile(r'!\[.*?\]\(.*?\)'), ''),  # Image
        (re.compile(r'\[(.*?)\]\(.*?\)'), r'\1'),  # Link
        (re.compile(r'^\s*>+\s*', re.MULTILINE), ''),  # Quote
        (
            re.compile(r'(?P<table_block>(?:^[^\n]*\|[^\n]*\n)+)', re.MULTILINE),
            _replace_table_block
        ),
        (re.compile(r'^\s*[*+-]\s*', re.MULTILINE), '- '),  # List
        (re.compile(r'\$\$.*?\$\$', re.DOTALL), ''),  # Block Formula
        (
            re.compile(r'(?<![A-Za-z0-9])\$([^\n$]+)\$(?![A-Za-z0-9])'),
            _replace_inline_dollar
        ),
        (re.compile(r'\n{2,}'), '\n'),  # Extra Blank Lines
    ]

    @staticmethod
    def clean_markdown(text: str) -> str:
        """
        Main entry method: run all regexes in order, remove or replace Markdown elements
        """
        for regex, replacement in MarkdownCleaner.REGEXES:
            text = regex.sub(replacement, text)

        # RemoveemojiEmoji
        text = check_emoji(text)

        # Check whether text all English and basic punctuation
        if text and all((c.isascii() or c.isspace() or c in punctuation_set) for c in text):
            # Keep original spaces, return directly
            return text

        return text.strip()

def convert_percentage_to_range(percentage, min_val, max_val, base_val=None):
    """
    Convert percentage (-100~100) to value in specified range

    Args:
        percentage: Percentage value (-100 to 100)
        min_val: Minimum target range value
        max_val: Maximum target range value
        base_val: Base value (optional, defaults to range midpoint)

    Returns:
        Converted value
    """
    percentage, min_val, max_val = float(percentage), float(min_val), float(max_val)
    base_val = float(base_val) if base_val is not None else (min_val + max_val) / 2

    if percentage < 0:
        # Negative percent: from base_val to min_val Linear Interpolation
        result = base_val + (base_val - min_val) * (percentage / 100)
    else:
        # Positive percent: from base_val to max_val Linear Interpolation
        result = base_val + (max_val - base_val) * (percentage / 100)

    # Ensure result within valid range
    return max(min_val, min(max_val, result))
