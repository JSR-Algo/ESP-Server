#!/usr/bin/env python3
"""Bounded signatures for audio content forbidden in redacted evidence."""

from __future__ import annotations

AUDIO_MAGIC = (b"RIFF", b"ID3", b"OggS", b"fLaC", b"ADIF")
ISO_AUDIO_BRANDS = {b"M4A ", b"M4B ", b"M4P ", b"F4A ", b"F4B "}
ISO_AUDIO_SAMPLE_ENTRIES = (b"mp4a", b"enca", b"alac", b"ac-3", b"ec-3", b"Opus", b"fLaC")


def _bounded_box(data: bytes, type_offset: int) -> tuple[int, int] | None:
    if type_offset < 4:
        return None
    start = type_offset - 4
    size = int.from_bytes(data[start:type_offset], "big")
    if size < 8 or size > len(data) - start:
        return None
    return start, start + size


def _iso_bmff_audio(data: bytes) -> bool:
    ftyp = data.find(b"ftyp", 4)
    box = _bounded_box(data, ftyp)
    if box is None:
        return False
    start, end = box
    brands = {data[offset : offset + 4] for offset in range(start + 8, end - 3, 4)}
    if brands & ISO_AUDIO_BRANDS:
        return True
    search_from = end
    for marker in ISO_AUDIO_SAMPLE_ENTRIES:
        offset = data.find(marker, search_from)
        while offset >= 0:
            if _bounded_box(data, offset) is not None:
                return True
            offset = data.find(marker, offset + 1)
    offset = data.find(b"hdlr", search_from)
    while offset >= 0:
        handler_box = _bounded_box(data, offset)
        if handler_box is not None and offset + 16 <= handler_box[1] and data[offset + 12 : offset + 16] == b"soun":
            return True
        offset = data.find(b"hdlr", offset + 1)
    return False


def contains_audio(data: bytes) -> bool:
    return (
        data.startswith(AUDIO_MAGIC)
        or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
        or (len(data) >= 2 and data[0] == 0x56 and data[1] & 0xE0 == 0xE0)
        or _iso_bmff_audio(data)
    )
