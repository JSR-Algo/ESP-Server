#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

DEFAULT_DEVICE_ID = "14:c1:9f:d1:a8:48"

SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(authorization|bearer|token|secret|password|code|challenge)=([^\s,;]+)"),
    re.compile(r"(?i)(device_secret|bootstrap_token|access_token|refresh_token)[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)(bearer)\s+([^\s,'\"}]+)"),
)
CP7_SECRET_LEAK_PATTERNS = (
    re.compile(
        r"(?i)\bauthorization\s*[:=]\s*(?:bearer|basic)\s+"
        r"(?!<?redacted>?(?:\b|$))[^\s,'\"}]+"
    ),
    re.compile(r"(?i)\bbearer\s+(?!<?redacted>?(?:\b|$))[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{2,}\b"),
    re.compile(r"(?i)\b(?:cookie|set-cookie)\s*[:=]\s*(?!<?redacted>?(?:\b|$))[^\s;]+"),
    re.compile(r"(?i)\b(?:x-api-key|api[_-]?key|apikey)\s*[:=]\s*(?!<?redacted>?(?:\b|$))[^\s,;]+"),
    re.compile(
        r"(?i)\b(?:device_secret|bootstrap_token|access_token|refresh_token|device_token|jwt|password|secret)"
        r"\s*[:=]\s*(?!<?redacted>?(?:\b|$))[^\s,;]+"
    ),
    re.compile(r"postgres(?:ql)?://[^:\s/]+:[^@\s]+@[^\s]+", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
)
ASSIGNMENT_ID_PATTERN = re.compile(r"(?i)\bassignment[_-]?id[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)")
ASSIGNMENT_STATE_VALUE_PATTERN = re.compile(r"(?i)\bstate[\"']?\s*[:=]\s*[\"']?([a-z_]+)\b")
SESSION_ID_PATTERNS = (
    re.compile(r"(?i)\bsession[_-]?id[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"),
    re.compile(r"(?i)\bsession\s+id\s*[:=]\s*([^,\"'}\s;]+)"),
    re.compile(r"(?i)\bsession\s*[:=]\s*([^,\"'}\s;]+)"),
)
STEP_ID_PATTERNS = (
    re.compile(r"(?i)\bstep[_-]?id[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"),
)
LESSON_SEQUENCE_PATTERNS = (
    re.compile(r"(?i)\bsequence[\"']?\s*[:=]\s*[\"']?(\d+)\b"),
    re.compile(r"(?i)\bseq[\"']?\s*[:=]\s*[\"']?(\d+)\b"),
)
LESSON_ID_PATTERNS = (
    re.compile(r"(?i)\blesson[_-]?id[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"),
    re.compile(r"(?i)\blesson[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"),
)
COURSE_ID_PATTERNS = (
    re.compile(r"(?i)\bcourse[_-]?id[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"),
    re.compile(r"(?i)\bcourse[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"),
)
PROFILE_PATTERNS = (
    re.compile(r"(?i)\bprofile[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"),
)
PRIMARY_WORD_PATTERNS = (
    re.compile(r"(?i)\bprimary[_-]?word[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"),
)
ROBOT_STATE_PATTERNS = (
    re.compile(r"(?i)\brobot[_-]?state[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"),
)
COMPLETION_CLASS_PATTERNS = (
    re.compile(r"(?i)\bcompletion[_-]?class[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"),
)
ZERO_PAYLOAD_PATTERNS = (
    re.compile(r"(?i)\bbytes[\"']?\s*[:=]\s*[\"']?0\b"),
    re.compile(r"(?i)\bduration[\"']?\s*[:=]\s*[\"']?0\b"),
    re.compile(r"(?i)\bduration[_-]?ms[\"']?\s*[:=]\s*[\"']?0\b"),
    re.compile(r"(?i)\bdurationMs[\"']?\s*[:=]\s*[\"']?0\b"),
    re.compile(r"(?i)\bsamples[\"']?\s*[:=]\s*[\"']?0\b"),
    re.compile(r"(?i)\bframes[\"']?\s*[:=]\s*[\"']?0\b"),
    re.compile(r"(?i)\bframe[_-]?count[\"']?\s*[:=]\s*[\"']?0\b"),
)
SILENT_AUDIO_PATTERNS = (
    re.compile(r"(?i)\bmuted[\"']?\s*[:=]\s*(?:[\"']?true\b|1\b)"),
    re.compile(r"(?i)\bvolume[\"']?\s*[:=]\s*[\"']?0(?:\.0+)?(?![.\d])\b"),
    re.compile(r"(?i)\bvolume[_-]?pct[\"']?\s*[:=]\s*[\"']?0(?:\.0+)?(?![.\d])\b"),
    re.compile(r"(?i)\bvolume[_-]?percent[\"']?\s*[:=]\s*[\"']?0(?:\.0+)?(?![.\d])\b"),
    re.compile(r"(?i)\bspeaker[_-]?enabled[\"']?\s*[:=]\s*(?:[\"']?false\b|0\b)"),
    re.compile(r"(?i)\bspeaker[_-]?disabled[\"']?\s*[:=]\s*(?:[\"']?true\b|1\b)"),
    re.compile(
        r"(?i)\b(?:audio[_-]?route|audioRoute|audio[_-]?output|audioOutput|output[_-]?device|outputDevice)"
        r"[\"']?\s*[:=]\s*[\"']?(?:none|null|disabled|off)\b"
    ),
)
MANIFEST_CHECKSUM_PATTERNS = (
    re.compile(r"(?i)\bmanifest[_-]?checksum[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"),
)
# Manifest-pin abort: an explicit start-side mismatch marker. When the
# assignment checksum and the fetched manifest checksum disagree, the runtime
# must refuse to start (these are the markers that prove the refusal happened).
MANIFEST_PIN_MISMATCH_PATTERNS = (
    re.compile(r"(?i)assignment[_\s-]?manifest[_\s-]?checksum[_\s-]?mismatch"),
    re.compile(r"(?i)manifest[_\s-]?pin[_\s-]?mismatch"),
    re.compile(r"(?i)manifest[_\s-]?pin\b[^\n]*mismatch"),
    re.compile(
        r"(?i)lesson[_\s-]?start[_\s-]?(?:blocked|failed)\b[^\n]*"
        r"manifest[_\s-]?checksum[_\s-]?mismatch"
    ),
    re.compile(
        r"(?i)manifest[_\s-]?checksum[_\s-]?mismatch\b[^\n]*"
        r"lesson[_\s-]?start[_\s-]?(?:blocked|failed)"
    ),
)
# A surfaced start-status error: the runtime told the caller the start failed
# rather than silently swallowing the mismatch.
MANIFEST_PIN_START_STATUS_ERROR_PATTERNS = (
    re.compile(r"(?i)manifest[_\s-]?checksum[_\s-]?mismatch"),
    re.compile(r"(?i)lesson[_\s-]?start[_\s-]?blocked"),
    re.compile(r"(?i)lesson[_\s-]?start[_\s-]?failed"),
    re.compile(r"(?i)start[_\s-]?status\b[^\n]*error"),
    re.compile(r"(?i)error\b[^\n]*start[_\s-]?status"),
    re.compile(r"(?i)[\"']type[\"']\s*:\s*[\"']lesson_error[\"']"),
    re.compile(r"(?i)\b(?:server send|serial rx|rx)\s+lesson_error\b"),
    re.compile(r"(?i)manifest[_\s-]?pin\b[^\n]*mismatch"),
    re.compile(r"(?i)không khớp checksum"),
    re.compile(r"(?i)khong khop checksum"),
)
CACHE_KEY_PATTERNS = (
    re.compile(r"(?i)\bcache[_-]?key[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"),
)
CHILD_ID_PATTERNS = (
    re.compile(r"(?i)\bchild[_-]?id[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"),
)
LESSON_VERSION_PATTERNS = (
    re.compile(r"(?i)\blesson[_-]?version[\"']?\s*[:=]\s*[\"']?(\d+)\b"),
)
ASSIGNMENT_VERSION_PATTERNS = (
    re.compile(r"(?i)\bassignment[_-]?version[\"']?\s*[:=]\s*[\"']?(\d+)\b"),
)
STORY_EVIDENCE_PATTERNS = (
    re.compile(r"(?i)\bstory[_-]?beat\b"),
    re.compile(r"(?i)[\"']storyBeat[\"']\s*:"),
    re.compile(r"(?i)\bstory[_-]?text\b"),
    re.compile(r"(?i)[\"']storyText[\"']\s*:"),
    re.compile(r"(?i)[\"']story[\"']\s*:"),
    re.compile(r"(?i)\bnarrative\b"),
    re.compile(r"(?i)\bbeat[_-]?id\b"),
)
STORY_WAIT_FOR_CHILD_PATTERNS = (
    re.compile(r"(?i)[\"']waitForChild[\"']\s*:\s*(?:true|1)\b"),
    re.compile(r"(?i)\bwaitForChild\b\s*[=:]\s*(?:true|1)\b"),
    re.compile(r"(?i)[\"']wait_for_child[\"']\s*:\s*(?:true|1)\b"),
    re.compile(r"(?i)\bwait_for_child\b\s*[=:]\s*(?:true|1)\b"),
)
# Lowercase-hex digest (sha256-shaped: 8+ hex chars). Matches the manifest's
# lowercase hex `sha256` asset checksums.
_HEX_DIGEST_PATTERN = re.compile(r"[0-9a-f]{8,}")
# Canonical SD cache directory shape "<lesson>/v<version>-<checksum>": the last
# path segment is "v" + an integer version + "-" + the manifest checksum tail.
CACHE_KEY_VERSION_SEGMENT_PATTERN = re.compile(r"(?i)/v(\d+)-([0-9a-z]+)/?$")
ASSET_PACK_READY_PATTERNS = (
    re.compile(r"(?i)\basset[_-]?pack\.ready[\"']?\s*[:=]\s*(?:[\"']?true\b|1\b|ready\b)"),
    re.compile(r"(?i)\basset[_-]?pack[_-]?ready[\"']?\s*[:=]\s*(?:[\"']?true\b|1\b|ready\b)"),
)
MEDIA_URL_PATTERN = re.compile(r"(?:https?|sd|file)://[^\s\"'}]+")
HTTP_URL_PATTERN = re.compile(r"https?://[^\s\"'}]+")
LESSON_STEP_REQUIRED_SOURCE_PATHS = (
    "backgroundScene.poster.src",
    "teachingObject.asset.src",
    "robotOverlay.asset.src",
)
MAX_LESSON_FRAME_BYTES = 16 * 1024
INLINE_MEDIA_PAYLOAD_KEYS = {
    "bytes",
    "bytearray",
    "base64",
    "payload",
    "imagebytes",
    "imagedata",
    "datauri",
}
LESSON_STEP_TEXT_SOURCE_PATTERNS = tuple(
    (path, re.compile(rf"(?i)(?<![A-Za-z0-9_.-]){re.escape(path)}[\"']?\s*[:=]\s*[\"']?([^\s\"';}}]+)"))
    for path in LESSON_STEP_REQUIRED_SOURCE_PATHS
)
LESSON_STEP_TEXT_KEY_PATTERNS = {
    path: tuple(
        re.compile(rf"(?i)(?<![A-Za-z0-9_.-]){re.escape(field)}[\"']?\s*[:=]\s*[\"']?([^\s\"';}}]+)")
        for field in fields
    )
    for path, fields in {
        "backgroundScene.poster.src": (
            "backgroundScene.poster.key",
            "backgroundScene.poster.assetKey",
            "backgroundScene.poster.asset_key",
        ),
        "teachingObject.asset.src": (
            "teachingObject.asset.key",
            "teachingObject.asset.assetKey",
            "teachingObject.asset.asset_key",
        ),
        "robotOverlay.asset.src": (
            "robotOverlay.asset.key",
            "robotOverlay.asset.assetKey",
            "robotOverlay.asset.asset_key",
        ),
    }.items()
}
FAILURE_STATUS_PATTERN = re.compile(
    r"(?i)(?:\bstatus[\"']?\s*[:=]\s*[\"']?[45]\d\d\b|->\s*[45]\d\d\b)"
)
FATAL_ERROR_PATTERNS = (
    re.compile(r"(?i)wifi.*disconnect"),
    re.compile(r"(?i)wifi.*fail"),
    re.compile(r"(?i)wifi.*timeout"),
    re.compile(r"(?i)không kết nối wifi"),
    re.compile(r"(?i)khong ket noi wifi"),
    re.compile(r"(?i)wifi mất kết nối"),
    re.compile(r"(?i)wifi mat ket noi"),
    re.compile(r"(?i)server unavailable"),
    re.compile(r"(?i)máy chủ không khả dụng"),
    re.compile(r"(?i)may chu khong kha dung"),
    re.compile(r"(?i)no current assignment"),
    re.compile(r"(?i)no_current_assignment"),
    re.compile(r"(?i)chưa có bài học"),
    re.compile(r"(?i)chua co bai hoc"),
    re.compile(r"(?i)không có bài học"),
    re.compile(r"(?i)khong co bai hoc"),
    re.compile(r"(?i)không có assignment"),
    re.compile(r"(?i)khong co assignment"),
    re.compile(r"(?i)lesson_start_failed"),
    re.compile(r"(?i)[\"']type[\"']\s*:\s*[\"']lesson_error[\"']"),
    re.compile(r"(?i)\b(?:server send|serial rx|rx)\s+lesson_error\b"),
    re.compile(r"(?i)\blesson[_\s-]?failed\b"),
    re.compile(r"(?i)\blesson[_\s-]?abandoned\b"),
    re.compile(r"(?i)\blesson[_\s-]?paused\b"),
    re.compile(r"(?i)\brobot[_\s-]?busy\b"),
    re.compile(r"(?i)\blow[_\s-]?battery\b"),
    re.compile(r"(?i)\bbattery[_\s-]?low\b"),
    re.compile(r"(?i)\blow[_\s-]?power\b"),
    re.compile(r"(?i)pin yếu"),
    re.compile(r"(?i)pin yeu"),
    re.compile(r"(?i)\brobot[_\s-]?(offline|unavailable)\b"),
    re.compile(r"(?i)manifest (fetch|load).*fail"),
    re.compile(r"(?i)asset[_\s-]?pack[_\s-]?not[_\s-]?ready"),
    re.compile(r"(?i)asset_checksum_mismatch"),
    re.compile(r"(?i)asset_download_failed"),
    re.compile(r"(?i)asset_profile_unavailable"),
    re.compile(r"(?i)preload_timeout"),
    re.compile(r"(?i)checksum mismatch"),
    re.compile(r"(?i)preload.*timeout"),
    re.compile(r"(?i)asset .*download.*fail"),
    re.compile(r"(?i)step_timeout"),
    re.compile(r"(?i)ack.*timeout"),
    re.compile(r"(?i)timeout.*ack"),
    re.compile(r"(?i)waiting for robot.*(confirm|auth)"),
    re.compile(r"(?i)đang chờ.*robot.*xác (nhận|thực)"),
    re.compile(r"(?i)dang cho.*robot.*xac (nhan|thuc)"),
    re.compile(r"(?i)waiting for robot.*timeout"),
    re.compile(r"(?i)hết thời gian chờ.*robot.*xác nhận"),
    re.compile(r"(?i)het thoi gian cho.*robot.*xac nhan"),
    re.compile(r"(?i)render .*fail"),
    re.compile(r"(?i)poster .*fail"),
    re.compile(r"(?i)lesson[_\s-]?completed.*\b(fail(?:ed|ure)?|error|completed\s*[:=]\s*false|success\s*[:=]\s*false)\b"),
    re.compile(r"(?i)\b(fail(?:ed|ure)?|error|completed\s*[:=]\s*false|success\s*[:=]\s*false)\b.*lesson[_\s-]?completed"),
    re.compile(r"(?i)(audio|tts|speaker|voice).*fail"),
    re.compile(r"(?i)(audio|tts|speaker|voice).*error"),
    re.compile(r"(?i)no audio"),
    re.compile(r"(?i)silent response"),
    re.compile(r"(?i)không nghe"),
    re.compile(r"(?i)khong nghe"),
    re.compile(r"(?i)websocket .*disconnect"),
    re.compile(r"(?i)websocket .*closed"),
)
BENIGN_FATAL_ERROR_CONTEXT_PATTERNS = (
    re.compile(r"(?i)\brx_bcn_pti\b.*\bbcn_timeout\b"),
    re.compile(r"(?i)\bfallback_to_classic_on_error\b"),
    re.compile(r"(?i)\bsetting wifi power save level\b"),
)
DEGRADED_RENDER_PATTERNS = (
    re.compile(r"(?i)\bdegraded\s*=\s*true\b"),
    re.compile(r"(?i)\bdegraded\s*=\s*1\b"),
    re.compile(r"(?i)[\"']degraded[\"']\s*:\s*true\b"),
    re.compile(r"(?i)[\"']degraded[\"']\s*:\s*1\b"),
    re.compile(r"(?i)\bdegraded\s+true\b"),
    re.compile(r"(?i)\bdegraded\s+1\b"),
)
PASSIVE_STEP_RENDER_PATTERNS = (
    re.compile(r"(?i)\blesson[_\s-]?step\b.*\brendered\b.*\bpassive\s*=\s*(?:true|1)\b"),
    re.compile(r"(?i)\blesson[_\s-]?step\b.*\brendered\b.*[\"']passive[\"']\s*:\s*(?:true|1)\b"),
    re.compile(r"(?i)\blesson[_\s-]?step\b.*\brendered\b.*\bpassive\s+(?:true|1)\b"),
)
FALLBACK_RENDER_PATTERNS = (
    re.compile(r"(?i)primitive[_-]?fallback[_-]?card"),
    re.compile(r"(?i)\bfallback\s*=\s*(true|1)\b"),
    re.compile(r"(?i)[\"']fallback[\"']\s*:\s*(true|1)\b"),
    re.compile(r"(?i)\bfallback\b.*\b(background|poster|video|render)"),
    re.compile(r"(?i)\b(default|idle|placeholder)\b.*\b(background|poster|video|render)"),
    re.compile(r"(?i)\b(background|poster|video|render)\b.*\b(fallback|default|idle|placeholder)\b"),
)
IMMEDIATE_PRONUNCIATION_SCORING_PATTERNS = (
    re.compile(r"(?i)\bpronunciation\b.*\b(score|scoring|grade|grading|evaluate|evaluating|assess|assessing|correct|correction)\b"),
    re.compile(r"(?i)\b(score|scoring|grade|grading|evaluate|evaluating|assess|assessing|correct|correction)\b.*\bpronunciation\b"),
    re.compile(r"(?i)\bpronunciation[_-]?score\b"),
    re.compile(r"(?i)\bpronounce\b.*\b(score|scoring|grade|grading|evaluate|assess|correct)\b"),
    re.compile(r"(?i)\b(score|scoring|grade|grading|evaluate|assess|correct)\b.*\bpronounce\b"),
    re.compile(r"(?i)\b(final consonant|ending sound)\b"),
    re.compile(r"(?i)\b(chấm|cham|đánh giá|danh gia|sửa|sua)\b.*\b(phát âm|phat am)\b"),
    re.compile(r"(?i)\b(phát âm|phat am)\b.*\b(chấm|cham|đánh giá|danh gia|sửa|sua)\b"),
    re.compile(r"(?i)\b(phát âm|phat am)\b.*\b(chưa chuẩn|chua chuan|không chuẩn|khong chuan|sai|lỗi|loi)\b"),
    re.compile(r"(?i)\b(chưa chuẩn|chua chuan|không chuẩn|khong chuan|sai|lỗi|loi)\b.*\b(phát âm|phat am)\b"),
    re.compile(r"(?i)\b(phát âm|phat am)\b.*\b(chuẩn|chuan|tốt|tot|hay|giỏi|gioi)\b"),
    re.compile(r"(?i)\b(chuẩn|chuan|tốt|tot|hay|giỏi|gioi)\b.*\b(phát âm|phat am)\b"),
)
IMMEDIATE_CHILD_RESPONSE_EVALUATION_PATTERNS = (
    re.compile(r"(?i)\b(child response|child_response|recognized[_-]?text|transcript)\b.*\b(score|grade|grading|correct\s*[:=]\s*(?:true|false|0|1))\b"),
    re.compile(r"(?i)\b(score|grade|grading|correct\s*[:=]\s*(?:true|false|0|1))\b.*\b(child response|child_response|recognized[_-]?text|transcript)\b"),
)
SPLIT_LINE_CHILD_RESPONSE_EVALUATION_PATTERNS = (
    re.compile(r"(?i)\bscore\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\bgrade\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\bgrading\b"),
    re.compile(r"(?i)\bcorrect\s*[:=]\s*(?:true|false|0|1)\b"),
    re.compile(r"(?i)\b(?:wrong|incorrect|not\s+(?:right|correct)|try\s+again)\b"),
    re.compile(r"(?i)\b(?:sai|đúng|dung)\s*(?:rồi|roi)\b"),
    re.compile(r"(?i)\b(?:chưa đúng|chua dung|không đúng|khong dung)\b"),
)
GUIDED_SPEAKING_PROMPT_PATTERNS = (
    re.compile(r"\?"),
    re.compile(r"(?i)\b(can you|could you|what do you|what can you|do you see|tell me)\b"),
    re.compile(r"(?i)\bwhere\s+is\b"),
    re.compile(r"(?i)\bhow\s+(?:many|much)\b"),
    re.compile(r"(?i)\b(which|who|why)\s+(?:is|are|animal|object|one|sound|color|do|can|would|should|did)\b"),
    re.compile(r"(?i)\b(con có|con thay|con thấy|bé có|be co|be thay|noi cho|nói cho)\b"),
)
COMMAND_ONLY_PROMPT_PATTERNS = (
    re.compile(r"(?i)\btext\s*=\s*[\"']?\s*(?:please\s+)?(?:say|repeat|listen|look|touch|tap)\b"),
    re.compile(r"(?i)[\"']text[\"']\s*:\s*[\"']\s*(?:please\s+)?(?:say|repeat|listen|look|touch|tap)\b"),
    re.compile(r"(?i)\btext\s*=\s*[\"']?\s*(?:noi|nói|nhac|nhắc|lap|lặp)\b"),
    re.compile(r"(?i)[\"']text[\"']\s*:\s*[\"']\s*(?:noi|nói|nhac|nhắc|lap|lặp)\b"),
    re.compile(r"(?i)\btext\s*=\s*[\"']?\s*(?:hay|hãy)\s+(?:say|repeat|listen|look|touch|tap|noi|nói|nhac|nhắc|lap|lặp)\b"),
    re.compile(r"(?i)[\"']text[\"']\s*:\s*[\"']\s*(?:hay|hãy)\s+(?:say|repeat|listen|look|touch|tap|noi|nói|nhac|nhắc|lap|lặp)\b"),
)
RAW_COMMAND_ONLY_PROMPT_PATTERNS = (
    re.compile(r"(?i)^\s*(?:please\s+)?(?:say|repeat|listen|look|touch|tap)\b"),
    re.compile(r"(?i)^\s*(?:noi|nói|nhac|nhắc|lap|lặp)\b"),
    re.compile(r"(?i)^\s*(?:hay|hãy)\s+(?:say|repeat|listen|look|touch|tap|noi|nói|nhac|nhắc|lap|lặp)\b"),
)
PROGRESS_SUCCESS_PATTERNS = (
    re.compile(r"(?i)\bresult\s*=\s*success\b"),
    re.compile(r"(?i)[\"']result[\"']\s*:\s*[\"']success[\"']"),
    re.compile(r"(?i)\bresult\s+success\b"),
)
EXPECTED_STEP_COUNT_PATTERNS = (
    re.compile(r"(?i)\btotal[_-]?steps\s*[:=]\s*[\"']?(\d+)\b"),
    re.compile(r"(?i)[\"']totalSteps[\"']\s*:\s*[\"']?(\d+)\b"),
    re.compile(r"(?i)\bstep[_-]?count\s*[:=]\s*[\"']?(\d+)\b"),
    re.compile(r"(?i)[\"']stepCount[\"']\s*:\s*[\"']?(\d+)\b"),
)
JSON_OBJECT_START_PATTERN = re.compile(r"{")
IP_ADDRESS_PATTERN = re.compile(r"\b(?:ip|ip_addr|ip address)\s*[:=]?\s*(?:\d{1,3}\.){3}\d{1,3}\b", re.IGNORECASE)
ACTIVE_ASSIGNMENT_STATE_PATTERN = re.compile(
    r"(?i)\b(state\s*=\s*|state[\"']?\s*:\s*[\"']?)(assigned|preloading|ready|running|paused)\b"
)
EXPLICIT_DEVICE_ID_PATTERN = re.compile(
    r"(?i)(?<!backend_)(?<!backend-)\b(device[_-]?id|deviceid|mac|device)[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"
)
BACKEND_DEVICE_ID_PATTERN = re.compile(
    r"(?i)\bbackend[_-]?device[_-]?id[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"
)
BACKEND_URL_FIELD_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?base|backend[_-]?url|base[_-]?url|lesson\s+api)"
    r"[\"']?\s*[:=]\s*[\"']?(https?://[^\s\"'}]+)"
)
LESSON_API_ENDPOINT_PATTERN = re.compile(
    r"(?i)^https?://[^/]+/(?:v\d+/)?(?:"
    r"devices/[^/?#]+/(?:assignment|assignments)/current|"
    r"lessons(?:[/ ?#]|$)|lesson-(?:assignments|events|assets)(?:[/ ?#]|$)"
    r")"
)
NO_CURRENT_ASSIGNMENT_STATUS_PATTERNS = (
    re.compile(r"(?i)\bno[_\s-]?current[_\s-]?assignment\b"),
    re.compile(r"(?i)\bno[_\s-]?(active[_\s-]?)?assignment\b"),
    re.compile(r"(?i)chưa có bài học"),
    re.compile(r"(?i)chua co bai hoc"),
    re.compile(r"(?i)không có bài học"),
    re.compile(r"(?i)khong co bai hoc"),
    re.compile(r"(?i)không có assignment"),
    re.compile(r"(?i)khong co assignment"),
)


def _norm(value: str) -> str:
    return value.strip().lower()


def _backend_or_server_source(lowered: str) -> bool:
    return any(
        token in lowered
        for token in (
            "backend ",
            "backend_",
            "backend.",
            "backend:",
            "server send",
            "server_send",
            "server.send",
            "server:",
            "server post",
            "server_post",
            "server.post",
            "server persist",
            "server_persist",
            "server.persist",
            "server ack",
            "server_ack",
            "server.ack",
        )
    )


def _backend_source(lowered: str) -> bool:
    return any(
        token in lowered
        for token in (
            "backend ",
            "backend_",
            "backend.",
            "backend:",
        )
    )


def _redact_match(match: re.Match[str]) -> str:
    if len(match.groups()) == 2:
        return f"{match.group(1)}=<redacted>"
    return "<redacted>"


def redact_line(line: str) -> str:
    redacted = line
    for pattern in SENSITIVE_PATTERNS:
        redacted = pattern.sub(_redact_match, redacted)
    return redacted


def _mentions_any(line: str, needles: Iterable[str]) -> bool:
    haystack = _norm(line)
    return any(_norm(needle) in haystack for needle in needles if needle)


def _contains_all(*needles: str) -> Callable[[str], bool]:
    lowered = tuple(_norm(needle) for needle in needles)
    return lambda line: all(needle in _norm(line) for needle in lowered)


def _contains_any(*needles: str) -> Callable[[str], bool]:
    lowered = tuple(_norm(needle) for needle in needles)
    return lambda line: any(needle in _norm(line) for needle in lowered)


def _and(*predicates: Callable[[str], bool]) -> Callable[[str], bool]:
    return lambda line: all(predicate(line) for predicate in predicates)

def _robot_booted(line: str) -> bool:
    lowered = _norm(line)
    if "websocket" in lowered or "lesson_" in lowered:
        return False
    return any(
        token in lowered
        for token in (
            "firmware boot",
            "boot complete",
            "application start",
            "application started",
            "app_main",
            "rst:",
        )
    )

def _websocket_connected(line: str) -> bool:
    lowered = _norm(line)
    if "websocket" not in lowered:
        return False
    if any(
        token in lowered
        for token in (
            "disconnect",
            "closed",
            "offline",
            "fail",
            "failed",
            "error",
            "timeout",
            "unavailable",
            "refused",
            "expired",
            "opened=false",
            '"opened":false',
            "opened false",
            "opened=0",
            '"opened":0',
            "connected=false",
            '"connected":false',
            "connected false",
            "connected=0",
            '"connected":0',
            "state=connecting",
            '"state":"connecting"',
            "state=opening",
            '"state":"opening"',
            "state=pending",
            '"state":"pending"',
            "handshake=pending",
            '"handshake":"pending"',
            "handshake pending",
            "authenticating",
        )
    ):
        return False
    return any(token in lowered for token in ("hello", "online", " connected", "session", "opened"))

def _wifi_connected(line: str) -> bool:
    lowered = _norm(line)
    if any(
        token in lowered
        for token in (
            "disconnect",
            "disconnected",
            "fail",
            "failed",
            "timeout",
            "offline",
            "not connected",
            "connected=false",
            '"connected":false',
            "connected false",
            "ip=0.0.0.0",
            "ip:0.0.0.0",
            "ip 0.0.0.0",
        )
    ):
        return False
    if "wifi" in lowered and "connected" in lowered and ("ssid" in lowered or IP_ADDRESS_PATTERN.search(line)):
        return True
    if "sta" in lowered and "got ip" in lowered:
        return True
    if "wifi" in lowered and "got ip" in lowered:
        return True
    return False

# Positive start-lesson phrases the robot should act on. Stored both with
# diacritics and ASCII-folded so captured logs in either form are recognized.
LESSON_START_POSITIVE_PHRASES = (
    "bắt đầu bài học",
    "bat dau bai hoc",
    "vào khóa học",
    "vao khoa hoc",
    "tiếp tục khóa học",
    "tiep tuc khoa hoc",
    "start lesson",
    "start the lesson",
    "continue the course",
    "open my course",
)
# Negation markers that, when paired with a positive phrase, mean "do NOT start".
LESSON_START_NEGATION_MARKERS = (
    "không",
    "khong",
    "đừng",
    "dung",
    "do not",
    "don't",
    "dont",
    "never",
    "stop",
)


def _has_positive_start_phrase(lowered: str) -> bool:
    return any(phrase in lowered for phrase in LESSON_START_POSITIVE_PHRASES)


def _has_negated_start_phrase(lowered: str) -> bool:
    """A negative start phrase carries a positive phrase prefixed by a negation marker
    (e.g. "không vào khóa học"), so it must NOT be read as a start request even though
    it contains the positive substring."""
    if not _has_positive_start_phrase(lowered):
        return False
    for marker in LESSON_START_NEGATION_MARKERS:
        marker_index = lowered.find(marker)
        if marker_index == -1:
            continue
        for phrase in LESSON_START_POSITIVE_PHRASES:
            phrase_index = lowered.find(phrase)
            if phrase_index != -1 and marker_index < phrase_index:
                return True
    return False


def _lesson_start_requested(line: str) -> bool:
    lowered = _norm(line)
    if _backend_or_server_source(lowered):
        return False
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "error",
            "timeout",
            "not handled",
            "ignored",
            "handled=false",
            '"handled":false',
            "handled=0",
            '"handled":0',
            "handled false",
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted true",
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted true",
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped true",
            "stopped=1",
            '"stopped":1',
        )
    ):
        return False
    # A negated start phrase ("không vào khóa học") is a "do not start" utterance and is
    # never a positive start request, even if a start_lesson tool token also appears on
    # the line (that contradiction is flagged by the scope gate).
    if _has_negated_start_phrase(lowered):
        return False
    if "start_lesson" in lowered:
        return any(
            token in lowered
            for token in (
                "intent",
                "tool",
                "function",
                "command",
                "dispatch",
                "handled",
                "local tool",
            )
        )
    if _has_positive_start_phrase(lowered):
        return any(token in lowered for token in ("intent", "command", "dispatch", "handled", "tool"))
    return False

def _no_current_assignment_status(line: str) -> bool:
    """Affirmative: the robot/server reported a no-current-assignment status.

    No-assignment scenario: clearing the active assignment then saying the start phrase must
    surface a no-current-assignment status rather than starting a lesson. This
    matches the status/say emission (not just any incidental error string) and
    rejects shapes where a different active assignment was actually found.
    """
    lowered = _norm(line)
    if not any(pattern.search(line) for pattern in NO_CURRENT_ASSIGNMENT_STATUS_PATTERNS):
        return False
    # A surfaced no-current-assignment status must not co-exist with an active
    # assignment id / assigned-state on the same line (that would be a real run).
    if ASSIGNMENT_ID_PATTERN.search(line) and ACTIVE_ASSIGNMENT_STATE_PATTERN.search(line):
        return False
    return any(
        token in lowered
        for token in (
            "status=",
            '"status"',
            "status ",
            "reason=",
            '"reason"',
            "voice say",
            "tts",
            "say text",
            "->",
            "assignment=null",
            '"assignment":null',
            "aborted",
            "no_current_assignment",
            "no current assignment",
        )
    )

def _lesson_start_acknowledged(line: str) -> bool:
    lowered = _norm(line)
    if _backend_or_server_source(lowered):
        return False
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "error",
            "timeout",
            "silent",
            "no audio",
            "không nghe",
            "khong nghe",
            "complete=false",
            '"complete":false',
            "complete false",
            "complete=0",
            '"complete":0',
            "played=false",
            '"played":false',
            "played false",
            "played=0",
            '"played":0',
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted true",
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted true",
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped true",
            "stopped=1",
            '"stopped":1',
        )
    ):
        return False
    if any(pattern.search(line) for pattern in ZERO_PAYLOAD_PATTERNS):
        return False
    if any(pattern.search(line) for pattern in SILENT_AUDIO_PATTERNS):
        return False
    played = any(
        token in lowered
        for token in (
            "audible ack played",
            "tts played",
            "tts playback complete",
            "audio played",
            "audio playback done",
            "audio playback complete",
            "speaker played",
            "voice response played",
            "say complete",
            "speech played",
        )
    )
    if not played:
        return False
    if "start_lesson" in lowered:
        return True
    if "bắt đầu bài học" in lowered or "bat dau bai hoc" in lowered or "start lesson" in lowered:
        return True
    return False

def _positive_frame(frame_type: str) -> Callable[[str], bool]:
    normalized_frame = _norm(frame_type)
    json_type = f'"type":"{normalized_frame}"'

    def predicate(line: str) -> bool:
        lowered = _norm(line)
        if normalized_frame not in lowered:
            return False
        if any(
            token in lowered
            for token in (
                "not sent",
                "not forwarded",
                "missing",
                "failed",
                "fail",
                "sent=false",
                '"sent":false',
                "sent false",
                "sent=0",
                '"sent":0',
                "forwarded=false",
                '"forwarded":false',
                "delivered=false",
                '"delivered":false',
                "cancelled=true",
                '"cancelled":true',
                "cancelled true",
                "cancelled=1",
                '"cancelled":1',
                "canceled=true",
                '"canceled":true',
                "canceled true",
                "canceled=1",
                '"canceled":1',
                "aborted=true",
                '"aborted":true',
                "aborted true",
                "aborted=1",
                '"aborted":1',
                "interrupted=true",
                '"interrupted":true',
                "interrupted true",
                "interrupted=1",
                '"interrupted":1',
                "stopped=true",
                '"stopped":true',
                "stopped true",
                "stopped=1",
                '"stopped":1',
            )
        ):
            return False
        if any(
            isinstance(value, dict) and _norm(str(value.get("type"))) == normalized_frame
            for value in _json_values_from_line(line)
        ):
            return True
        return any(
            token in lowered
            for token in (
                json_type,
                f"type={normalized_frame}",
                f"server send {normalized_frame}",
                f"serial rx {normalized_frame}",
                f"rx {normalized_frame}",
            )
        )

    return predicate

def _sent_frame(frame_type: str) -> Callable[[str], bool]:
    normalized_frame = _norm(frame_type)
    json_type = f'"type":"{normalized_frame}"'

    def predicate(line: str) -> bool:
        lowered = _norm(line)
        if normalized_frame not in lowered:
            return False
        if any(
            token in lowered
            for token in (
                "not sent",
                "not forwarded",
                "missing",
                "failed",
                "fail",
                "sent=false",
                '"sent":false',
                "sent false",
                "sent=0",
                '"sent":0',
                "forwarded=false",
                '"forwarded":false',
                "delivered=false",
                '"delivered":false',
                "cancelled=true",
                '"cancelled":true',
                "cancelled true",
                "cancelled=1",
                '"cancelled":1',
                "canceled=true",
                '"canceled":true',
                "canceled true",
                "canceled=1",
                '"canceled":1',
                "aborted=true",
                '"aborted":true',
                "aborted true",
                "aborted=1",
                '"aborted":1',
                "interrupted=true",
                '"interrupted":true',
                "interrupted true",
                "interrupted=1",
                '"interrupted":1',
                "stopped=true",
                '"stopped":true',
                "stopped true",
                "stopped=1",
                '"stopped":1',
            )
        ):
            return False
        return any(
            token in lowered
            for token in (
                json_type,
                f"type={normalized_frame}",
                f"server send {normalized_frame}",
            )
        )

    return predicate

def _ack_count(count: int) -> Callable[[str], bool]:
    patterns = (
        re.compile(rf"(?i)\backs\s*=\s*{count}\b"),
        re.compile(rf"(?i)[\"']acks[\"']\s*:\s*{count}\b"),
        re.compile(rf"(?i)\backs\s+{count}\b"),
    )
    return lambda line: any(pattern.search(line) for pattern in patterns)

def _ack_number_from_line(line: str) -> int | None:
    patterns = (
        re.compile(r"(?i)\backs\s*=\s*(\d+)\b"),
        re.compile(r"(?i)[\"']acks[\"']\s*:\s*(\d+)\b"),
        re.compile(r"(?i)\backs\s+(\d+)\b"),
    )
    for pattern in patterns:
        match = pattern.search(line)
        if match:
            return int(match.group(1))
    return None

def _ack_frame_type(ack_number: int) -> str:
    if ack_number == 1:
        return "lesson_prepare"
    if ack_number == 2:
        return "lesson_start"
    return "lesson_step"

def _lesson_progress_success(line: str) -> bool:
    lowered = _norm(line)
    if "lesson_progress" not in lowered or "step_completed" not in lowered:
        return False
    if any(
        token in lowered
        for token in (
            "failed",
            "failure",
            "error",
            "timeout",
            '"result":"failed"',
            "success=false",
            '"success":false',
            "success false",
            "success=0",
            '"success":0',
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted true",
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted true",
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped true",
            "stopped=1",
            '"stopped":1',
        )
    ):
        return False
    return any(pattern.search(line) for pattern in PROGRESS_SUCCESS_PATTERNS)

def _backend_progress_posted(line: str) -> bool:
    lowered = _norm(line)
    if not _lesson_progress_success(line):
        return False
    if not _backend_source(lowered):
        return False
    if any(
        token in lowered
        for token in (
            "not posted",
            "not persisted",
            "not forwarded",
            "persisted=false",
            '"persisted":false',
            "persisted false",
            "persisted=0",
            '"persisted":0',
            "accepted=false",
            '"accepted":false',
            "accepted false",
            "failed",
            "failure",
            "error",
            "timeout",
        )
    ):
        return False
    if FAILURE_STATUS_PATTERN.search(line):
        return False
    return any(
        token in lowered
        for token in (
            "backend post",
            "backend posted",
            "backend persist",
            "backend persisted",
            "progress persisted",
            "progress forwarded",
            "posted lesson_progress",
            "persisted=true",
            "progressevent persisted",
        )
    )

def _robot_lesson_progress_success(line: str) -> bool:
    lowered = _norm(line)
    if not _lesson_progress_success(line):
        return False
    return not any(
        token in lowered
        for token in (
            "backend post",
            "backend posted",
            "backend persist",
            "backend persisted",
            "progress persisted",
            "progress forwarded",
            "posted lesson_progress",
            "persisted=true",
            "progressevent persisted",
        )
    )

def _lesson_audio_played(line: str) -> bool:
    lowered = _norm(line)
    if _backend_or_server_source(lowered):
        return False
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "error",
            "timeout",
            "silent",
            "no audio",
            "complete=false",
            '"complete":false',
            "complete false",
            "complete=0",
            '"complete":0',
            "played=false",
            '"played":false',
            "played false",
            "played=0",
            '"played":0',
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped=1",
            '"stopped":1',
            "reason=start_lesson_ack",
            '"reason":"start_lesson_ack"',
            "bytes=0",
            '"bytes":0',
            "duration_ms=0",
            '"duration_ms":0',
            "samples=0",
            '"samples":0',
        )
    ):
        return False
    if any(pattern.search(line) for pattern in ZERO_PAYLOAD_PATTERNS):
        return False
    if any(pattern.search(line) for pattern in SILENT_AUDIO_PATTERNS):
        return False
    return any(
        token in lowered
        for token in (
            "tts played",
            "tts playback complete",
            "audio played",
            "audio playback done",
            "audio playback complete",
            "speaker played",
            "voice response played",
            "say complete",
            "speech played",
        )
    )

def _lesson_step_prompt_handoff(line: str) -> bool:
    lowered = _norm(line)
    if "backend" in lowered:
        return False
    if "start_lesson" in lowered or "start lesson" in lowered or "bắt đầu bài học" in lowered or "bat dau bai hoc" in lowered:
        return False
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "failure",
            "error",
            "timeout",
            "handoff=0",
            '"handoff":0',
            "handoff=false",
            '"handoff":false',
            "queued=false",
            '"queued":false',
            "sent=false",
            '"sent":false',
            "spoken=false",
            '"spoken":false',
            "accepted=false",
            '"accepted":false',
        )
    ):
        return False
    step_prompt_markers = (
        "lessonruntime step prompt",
        "lesson step prompt",
        "lesson_step_prompt",
        "step prompt",
        "speak_lesson_step_prompt",
    )
    if any(token in lowered for token in ("queued via live text", "queued via tts")):
        return any(marker in lowered for marker in step_prompt_markers)
    if any(token in lowered for token in ("tts sentence_start", "tts start")):
        return bool(_step_ids_from_evidence(line)) or any(
            marker in lowered for marker in step_prompt_markers
        )
    return any(
        token in lowered
        for token in step_prompt_markers
    )

def _guided_speaking_prompt_handoff(line: str) -> bool:
    if not _lesson_step_prompt_handoff(line):
        return False
    if any(pattern.search(line) for pattern in COMMAND_ONLY_PROMPT_PATTERNS):
        return False
    return any(pattern.search(line) for pattern in GUIDED_SPEAKING_PROMPT_PATTERNS)

def _interactive_child_response_window_opened(line: str) -> bool:
    lowered = _norm(line)
    if any(
        token in lowered
        for token in (
            "closed",
            "inactive",
            "failed",
            "failure",
            "error",
            "timeout",
            "opened=false",
            '"opened":false',
            "opened=0",
            '"opened":0',
            "listening=false",
            '"listening":false',
            "listening=0",
            '"listening":0',
            "ready=false",
            '"ready":false',
            "ready=0",
            '"ready":0',
            "rearm=false",
            '"rearm":false',
            "rearm=0",
            '"rearm":0',
        )
    ):
        return False
    return any(
        token in lowered
        for token in (
            "child response window opened",
            "child_response_window opened",
            "listening window opened",
            "lesson interactive listening ready",
            "lesson/manual listening rearm",
            "user_audio_window_open reason=lesson_child_response",
        )
    )

def _lesson_started(line: str) -> bool:
    lowered = _norm(line)
    if _backend_or_server_source(lowered):
        return False
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "error",
            "timeout",
            "not started",
            "blocked",
            "started=false",
            '"started":false',
            "started=0",
            '"started":0',
            "started false",
            "state=starting",
            '"state":"starting"',
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted true",
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted true",
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped true",
            "stopped=1",
            '"stopped":1',
        )
    ):
        return False
    if "lesson_started" in lowered or "lesson started" in lowered:
        return True
    if "lesson_start" in lowered and any(token in lowered for token in ("code=started", '"code":"started"', "status=started")):
        return True
    if "running" in lowered and any(
        token in lowered
        for token in (
            "lessonruntime",
            "lesson runtime",
            "lesson state",
            "lesson_state",
            "ready -> running",
        )
    ):
        return True
    return False

def _lesson_step_started(line: str) -> bool:
    lowered = _norm(line)
    if _backend_or_server_source(lowered):
        return False
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "error",
            "timeout",
            "not started",
            "blocked",
            "started=false",
            '"started":false',
            "started=0",
            '"started":0',
            "started false",
            "state=starting",
            '"state":"starting"',
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted true",
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted true",
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped true",
            "stopped=1",
            '"stopped":1',
        )
    ):
        return False
    if "step_started" in lowered or "step started" in lowered:
        return True
    if "lesson_progress" in lowered and "step_started" in lowered:
        return True
    return False

def _background_rendered(line: str) -> bool:
    lowered = _norm(line)
    if _backend_or_server_source(lowered):
        return False
    if any(
        token in lowered
        for token in (
            "rendered=false",
            '"rendered":false',
            "rendered false",
            "rendered=0",
            '"rendered":0',
            "drawn=false",
            '"drawn":false',
            "drawn false",
            "drawn=0",
            '"drawn":0',
            "displayed=false",
            '"displayed":false',
            "displayed false",
            "displayed=0",
            '"displayed":0',
            "visible=false",
            '"visible":false',
            "visible false",
            "visible=0",
            '"visible":0',
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped true",
            "stopped=1",
            '"stopped":1',
            "not rendered",
            "server send",
            "backend ",
            "manifest fetched",
            "preload_ready",
            "fail",
            "failed",
            "error",
            "timeout",
        )
    ):
        return False
    if any(pattern.search(line) for pattern in ZERO_PAYLOAD_PATTERNS):
        return False
    return _contains_any(
        "poster fetched+drawn",
        "setlessonbackground",
        "background rendered",
        "video rendered",
        "lesson_step rendered",
    )(line)

def _lesson_content_rendered(line: str) -> bool:
    lowered = _norm(line)
    if _backend_or_server_source(lowered):
        return False
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "error",
            "timeout",
            "fallback",
            "placeholder",
            "rendered=false",
            '"rendered":false',
            "rendered false",
            "rendered=0",
            '"rendered":0',
            "displayed=false",
            '"displayed":false',
            "displayed false",
            "displayed=0",
            '"displayed":0',
            "visible=false",
            '"visible":false',
            "visible false",
            "visible=0",
            '"visible":0',
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped true",
            "stopped=1",
            '"stopped":1',
            "not rendered",
            "server send",
            "backend ",
            "manifest fetched",
            "preload_ready",
        )
    ):
        return False
    return any(
        token in lowered
        for token in (
            "teachingobject rendered",
            "teaching object rendered",
            "primaryword rendered",
            "primary word rendered",
            "lesson content rendered",
            "subject rendered",
            "object rendered",
        )
    )

def _robot_overlay_rendered(line: str) -> bool:
    lowered = _norm(line)
    if _backend_or_server_source(lowered):
        return False
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "error",
            "timeout",
            "fallback",
            "placeholder",
            "rendered=false",
            '"rendered":false',
            "rendered=0",
            '"rendered":0',
            "rendered false",
            "displayed=false",
            '"displayed":false',
            "displayed=0",
            '"displayed":0',
            "displayed false",
            "visible=false",
            '"visible":false',
            "visible=0",
            '"visible":0',
            "visible false",
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped true",
            "stopped=1",
            '"stopped":1',
            "not rendered",
            "server send",
            "backend ",
            "manifest fetched",
            "preload_ready",
        )
    ):
        return False
    return any(
        token in lowered
        for token in (
            "robotoverlay rendered",
            "robot overlay rendered",
            "robotstate rendered",
            "robot state rendered",
            "pose rendered",
            "expression rendered",
            "overlay rendered",
        )
    )

def _lesson_step_rendered_ack(line: str) -> bool:
    lowered = _norm(line)
    if "lesson_ack" not in lowered:
        return False
    if _backend_or_server_source(lowered):
        return False
    if any(token in lowered for token in ("fail", "failed", "error", "timeout")):
        return False
    if any(pattern.search(line) for pattern in DEGRADED_RENDER_PATTERNS):
        return False
    if any(
        token in lowered
        for token in (
            "ack=false",
            '"ack":false',
            "ack false",
            "ack=0",
            '"ack":0',
            "acked=false",
            '"acked":false',
            "acked false",
            "acked=0",
            '"acked":0',
            "accepted=false",
            '"accepted":false',
            "accepted false",
            "accepted=0",
            '"accepted":0',
            "displayed=false",
            '"displayed":false',
            "displayed false",
            "displayed=0",
            '"displayed":0',
            "visible=false",
            '"visible":false',
            "visible false",
            "visible=0",
            '"visible":0',
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted true",
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted true",
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped true",
            "stopped=1",
            '"stopped":1',
        )
    ):
        return False
    return _contains_any("rendered=true", "rendered\":true", "rendered true")(line)

def _lesson_ack_positive(count: int) -> Callable[[str], bool]:
    def predicate(line: str) -> bool:
        lowered = _norm(line)
        if "lesson_ack" not in lowered:
            return False
        if _backend_or_server_source(lowered):
            return False
        if any(token in lowered for token in ("fail", "failed", "error", "timeout")):
            return False
        if any(
            token in lowered
            for token in (
                "ack=false",
                '"ack":false',
                "ack false",
                "ack=0",
                '"ack":0',
                "acked=false",
                '"acked":false',
                "acked false",
                "acked=0",
                '"acked":0',
                "accepted=false",
                '"accepted":false',
                "accepted false",
                "accepted=0",
                '"accepted":0',
                "cancelled=true",
                '"cancelled":true',
                "cancelled true",
                "cancelled=1",
                '"cancelled":1',
                "canceled=true",
                '"canceled":true',
                "canceled true",
                "canceled=1",
                '"canceled":1',
                "aborted=true",
                '"aborted":true',
                "aborted true",
                "aborted=1",
                '"aborted":1',
                "interrupted=true",
                '"interrupted":true',
                "interrupted true",
                "interrupted=1",
                '"interrupted":1',
                "stopped=true",
                '"stopped":true',
                "stopped true",
                "stopped=1",
                '"stopped":1',
            )
        ):
            return False
        return _ack_count(count)(line)

    return predicate

def _lesson_step_rendered_ack_with_robot_state(line: str) -> bool:
    if not _lesson_step_rendered_ack(line):
        return False
    return _contains_any("robotstate", "robot_state", "robot state")(line)

def _lesson_preload_ready(line: str) -> bool:
    lowered = _norm(line)
    runtime_owned_preload = (
        "lessonruntime" in lowered and "lesson_preload_ready" in lowered
    )
    if _backend_source(lowered):
        return False
    if _backend_or_server_source(lowered) and not runtime_owned_preload:
        return False
    status_text = re.sub(
        r'(?i)"?failed_?count"?\s*[:=]\s*0(?:\.0+)?\b',
        "",
        lowered,
    )
    if any(
        token in status_text
        for token in (
            "fail",
            "failed",
            "error",
            "timeout",
            "not ready",
            "ready=false",
            '"ready":false',
            "ready=0",
            '"ready":0',
            "ready false",
            "preload_ready=false",
            '"preload_ready":false',
            "preload_ready=0",
            '"preload_ready":0',
            "preload ready false",
            "partial=true",
            '"partial":true',
            "partial true",
            "complete=false",
            '"complete":false',
            "complete=0",
            '"complete":0',
            "complete false",
            "allready=false",
            '"allready":false',
            "allready=0",
            '"allready":0',
            "all ready false",
            "downloaded=false",
            '"downloaded":false',
            "downloaded=0",
            '"downloaded":0',
            "downloaded false",
            "verified=false",
            '"verified":false',
            "verified=0",
            '"verified":0',
            "verified false",
            "criticalassets=missing",
            '"criticalassets":"missing"',
            "critical assets missing",
            "missingassets=",
            '"missingassets"',
            "missing assets=",
            "missing assets:",
            "criticalassets=unavailable",
            '"criticalassets":"unavailable"',
            "critical assets unavailable",
            "assets missing",
            "asset missing",
            "degraded=true",
            '"degraded":true',
            "degraded true",
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted true",
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted true",
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped true",
            "stopped=1",
            '"stopped":1',
        )
    ):
        return False
    return any(
        token in lowered
        for token in (
            "preload_ready",
            "preload ready",
            "asset cache ready",
            "critical assets ready",
            "lesson assets ready",
            "lesson_preload_ready",
        )
    )

def _preload_failure_marker(line: str) -> bool:
    """A preload was interrupted/failed mid-download (preload-recovery premise).

    Matches the real ESP runtime/asset-cache markers emitted when internet is
    lost during asset download: the asset-cache ``network_error`` reason, the
    ``preload_failed`` / ``sd_asset_pack_preload_failed`` terminal notifications,
    the ``PRELOAD_TIMEOUT`` retryable error, or an explicit preload retry/resume
    attempt. A clean ``preload_ready`` is NOT a failure marker.
    """
    lowered = _norm(line)
    if _lesson_preload_ready(line):
        return False
    return any(
        token in lowered
        for token in (
            "preload_failed",
            "preload failed",
            "sd_asset_pack_preload_failed",
            "preload_timeout",
            "preload timeout",
            "asset_download_failed",
            "asset download failed",
            "network_error",
            "network error",
            "preload retry",
            "preload_retry",
            "preload resume",
            "preload_resume",
            "re-preload",
            "repreload",
        )
    )

def _lesson_stop_received(line: str) -> bool:
    lowered = _norm(line)
    if any(
        token in lowered
        for token in (
            "backend ",
            "backend_post",
            "server send",
            "server post",
            "server persist",
            "server ack",
            "server lesson_stop",
            "server lesson_stop_ack",
        )
    ):
        return False
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "error",
            "timeout",
            "not received",
            "received=false",
            '"received":false',
            "received false",
            "received=0",
            '"received":0',
            "ack=false",
            '"ack":false',
            "ack false",
            "ack=0",
            '"ack":0',
            "cleared=false",
            '"cleared":false',
            "cleared false",
            "cleared=0",
            '"cleared":0',
            "stopped=false",
            '"stopped":false',
            "stopped false",
            "stopped=0",
            '"stopped":0',
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted true",
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted true",
            "interrupted=1",
            '"interrupted":1',
        )
    ):
        return False
    if "lesson_stop" not in lowered and "stop ack" not in lowered:
        return False
    return any(
        token in lowered
        for token in (
            "serial rx lesson_stop",
            "rx lesson_stop",
            "lesson_stop ack",
            "lesson_stop_ack",
            "stop ack",
            "background cleared",
            "lesson stopped",
        )
    ) or (_contains_all("lesson_ack")(line) and _ack_count(4)(line))

def _lesson_completed_positive(line: str) -> bool:
    lowered = _norm(line)
    if "lesson_completed" not in lowered and "lesson completed" not in lowered:
        return False
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "failure",
            "error",
            "timeout",
            "not completed",
            "not forwarded",
            "not posted",
            "not persisted",
            "completed=false",
            '"completed":false',
            "completed=0",
            '"completed":0',
            "completed false",
            "complete=false",
            '"complete":false',
            "complete=0",
            '"complete":0',
            "complete false",
            "success=false",
            '"success":false',
            "success=0",
            '"success":0',
            "success false",
            "persisted=false",
            '"persisted":false',
            "accepted=false",
            '"accepted":false',
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted true",
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted true",
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped true",
            "stopped=1",
            '"stopped":1',
            "status=4",
            "status=5",
            "-> 4",
            "-> 5",
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "aborted=true",
            '"aborted":true',
            "aborted true",
            "interrupted=true",
            '"interrupted":true',
            "interrupted true",
            "stopped=true",
            '"stopped":true',
            "stopped true",
        )
    ):
        return False
    if FAILURE_STATUS_PATTERN.search(line):
        return False
    return any(
        token in lowered
        for token in (
            "event lesson_completed",
            "post lesson_completed",
            '"type":"lesson_completed"',
            "type=lesson_completed",
            "lesson_completed event",
        )
    )

def _backend_completion_posted(line: str) -> bool:
    lowered = _norm(line)
    if "lesson_completed" not in lowered and "lesson completed" not in lowered:
        return False
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "failure",
            "error",
            "timeout",
            "not posted",
            "not forwarded",
            "not persisted",
            "not completed",
            "persisted=false",
            '"persisted":false',
            "persisted false",
            "persisted=0",
            '"persisted":0',
            "completed=false",
            '"completed":false',
            "completed=0",
            '"completed":0',
            "completed false",
            "complete=false",
            '"complete":false',
            "complete=0",
            '"complete":0',
            "complete false",
            "success=false",
            '"success":false',
            "success=0",
            '"success":0',
            "success false",
            "accepted=false",
            '"accepted":false',
            "accepted=0",
            '"accepted":0',
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted true",
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted true",
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped true",
            "stopped=1",
            '"stopped":1',
            "rejected",
            "status=4",
            "status=5",
            "-> 4",
            "-> 5",
        )
    ):
        return False
    if FAILURE_STATUS_PATTERN.search(line):
        return False
    if any(token in lowered for token in ("post /lesson_completed -> 200", "post lesson_completed -> 200")):
        return True
    if "backend" not in lowered:
        return False
    return any(
        token in lowered
        for token in (
            "post lesson_completed",
            "posted lesson_completed",
            "lesson_completed persisted",
            "lesson_completed accepted",
            "completion posted",
            "completion accepted",
            "persisted=true",
            '"persisted":true',
            "accepted=true",
            '"accepted":true',
        )
    )

def _assignment_completed(line: str) -> bool:
    lowered = _norm(line)
    if "assignment/current" not in lowered and "get_current_assignment" not in lowered:
        return False
    if not ASSIGNMENT_ID_PATTERN.search(line):
        return False
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "error",
            "timeout",
            "completed=false",
            '"completed":false',
            "completed=0",
            '"completed":0',
            "completed false",
            "complete=false",
            '"complete":false',
            "complete=0",
            '"complete":0',
            "complete false",
            "success=false",
            '"success":false',
            "success=0",
            '"success":0',
            "success false",
            "persisted=false",
            '"persisted":false',
            "persisted=0",
            '"persisted":0',
            "persisted false",
            "finalized=false",
            '"finalized":false',
            "finalized=0",
            '"finalized":0',
            "finalized false",
            "accepted=false",
            '"accepted":false',
            "accepted=0",
            '"accepted":0',
            "accepted false",
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted true",
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted true",
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped true",
            "stopped=1",
            '"stopped":1',
            "archived=true",
            '"archived":true',
            "archived true",
            "archived=1",
            '"archived":1',
            "expired=true",
            '"expired":true',
            "expired true",
            "expired=1",
            '"expired":1',
            "cached=true",
            '"cached":true',
            "cached true",
            "cached=1",
            '"cached":1',
            "cache_hit=true",
            '"cache_hit":true',
            "cache_hit=1",
            '"cache_hit":1',
            "cache hit",
            "source=cache",
            '"source":"cache"',
            "source=local",
            '"source":"local"',
            "stale=true",
            '"stale":true',
            "stale true",
            "stale=1",
            '"stale":1',
            "offline=true",
            '"offline":true',
            "offline true",
            "offline=1",
            '"offline":1',
        )
    ):
        return False
    if FAILURE_STATUS_PATTERN.search(line):
        return False
    return any(
        token in lowered
        for token in (
            "state=completed",
            '"state":"completed"',
            "state completed",
        )
    )

def _active_assignment_current(line: str) -> bool:
    lowered = _norm(line)
    if "assignment/current" not in lowered and "get_current_assignment" not in lowered:
        return False
    if not ASSIGNMENT_ID_PATTERN.search(line):
        return False
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "failure",
            "error",
            "timeout",
            "assigned=false",
            '"assigned":false',
            "assigned=0",
            '"assigned":0',
            "active=false",
            '"active":false',
            "active=0",
            '"active":0',
            "available=false",
            '"available":false',
            "available=0",
            '"available":0',
            "status=4",
            "status=5",
            "-> 4",
            "-> 5",
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "archived=true",
            '"archived":true',
            "archived true",
            "archived=1",
            '"archived":1',
            "expired=true",
            '"expired":true',
            "expired true",
            "expired=1",
            '"expired":1',
            "completed=true",
            '"completed":true',
            "completed true",
            "completed=1",
            '"completed":1',
            "deleted=true",
            '"deleted":true',
            "deleted=1",
            '"deleted":1',
            "removed=true",
            '"removed":true',
            "removed=1",
            '"removed":1',
            "revoked=true",
            '"revoked":true',
            "revoked=1",
            '"revoked":1',
            "disabled=true",
            '"disabled":true',
            "disabled=1",
            '"disabled":1',
            "valid=false",
            '"valid":false',
            "valid false",
            "valid=0",
            '"valid":0',
            "cached=true",
            '"cached":true',
            "cached true",
            "cached=1",
            '"cached":1',
            "cache_hit=true",
            '"cache_hit":true',
            "cache_hit=1",
            '"cache_hit":1',
            "cache hit",
            "source=cache",
            '"source":"cache"',
            "source=local",
            '"source":"local"',
            "stale=true",
            '"stale":true',
            "stale true",
            "stale=1",
            '"stale":1',
            "offline=true",
            '"offline":true',
            "offline true",
            "offline=1",
            '"offline":1',
        )
    ):
        return False
    if FAILURE_STATUS_PATTERN.search(line):
        return False
    return ACTIVE_ASSIGNMENT_STATE_PATTERN.search(line) is not None


def _manifest_fetched_with_identity(line: str) -> bool:
    lowered = _norm(line)
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "failure",
            "error",
            "timeout",
            "fetched=false",
            '"fetched":false',
            "fetched false",
            "fetched=0",
            '"fetched":0',
            "loaded=false",
            '"loaded":false',
            "loaded false",
            "loaded=0",
            '"loaded":0',
            "valid=false",
            '"valid":false',
            "valid false",
            "valid=0",
            '"valid":0',
            "status=4",
            "status=5",
            "-> 4",
            "-> 5",
            "cancelled=true",
            '"cancelled":true',
            "cancelled true",
            "cancelled=1",
            '"cancelled":1',
            "canceled=true",
            '"canceled":true',
            "canceled true",
            "canceled=1",
            '"canceled":1',
            "aborted=true",
            '"aborted":true',
            "aborted true",
            "aborted=1",
            '"aborted":1',
            "interrupted=true",
            '"interrupted":true',
            "interrupted true",
            "interrupted=1",
            '"interrupted":1',
            "stopped=true",
            '"stopped":true',
            "stopped true",
            "stopped=1",
            '"stopped":1',
        )
    ):
        return False
    if FAILURE_STATUS_PATTERN.search(line):
        return False
    if not any(
        token in lowered
        for token in (
            "manifest fetched",
            "lesson manifest fetched",
            "lesson manifest loaded",
            "manifest loaded",
        )
    ):
        return False
    return any(
        token in lowered
        for token in (
            "lesson=",
            '"lesson"',
            "lessonid",
            "lesson_id",
            "course=",
            '"course"',
            "courseid",
            "course_id",
            "slug=",
        )
    )

def _check(name: str, evidence: str | None, missing: str) -> dict[str, Any]:
    return {
        "name": name,
        "ok": evidence is not None,
        "evidence": evidence,
        "missing": "" if evidence is not None else missing,
    }

def _cp7_log_secret_scrub_check(lines: list[str]) -> dict[str, Any]:
    offenders = [
        f"line {index}: <redacted> credential/PII marker"
        for index, line in enumerate(lines, start=1)
        if any(pattern.search(line) for pattern in CP7_SECRET_LEAK_PATTERNS)
    ]
    return {
        "name": "cp7_log_secret_scrub",
        "ok": not offenders,
        "evidence": offenders[:5],
        "missing": ""
        if not offenders
        else "CP-7 evidence logs contain unredacted credential/PII markers",
    }

def _cp7_panel_sidecar_evidence(line: str) -> bool:
    lowered = _norm(line)
    return (
        "cp7_panel_evidence" in lowered
        and "passive=true" in lowered
        and "interactive=true" in lowered
        and "st77922=true" in lowered
        and ("three_layer=true" in lowered or "three layers" in lowered or "three-layer" in lowered)
    )

def _cp7_conversation_idle_sidecar_evidence(line: str) -> bool:
    lowered = _norm(line)
    return (
        ("cp7_lifecycle_evidence" in lowered or "finish_lesson_mode" in lowered)
        and "conversation_mode_restored=true" in lowered
        and "idle_face_restored=true" in lowered
    )

def _cp8_alarm_snapshot_sidecar_evidence(line: str) -> bool:
    lowered = _norm(line)
    return (
        "cp8_alarm_snapshot" in lowered
        and ("p95_ms=" in lowered or "p95ms=" in lowered)
        and ("alarm_active=false" in lowered or "disabled=false" in lowered or "ok=true" in lowered)
    )

def _render_latency_audio_sidecar_evidence(line: str) -> bool:
    lowered = _norm(line)
    return (
        "cp7_render_fetch_evidence" in lowered
        and "render_latency_ms=" in lowered
        and "audio_glitch=0" in lowered
        and "decode_drop=0" in lowered
        and "encode_drop=0" in lowered
        and "stale_frames=0" in lowered
        and "interrupts=0" in lowered
    )

def _cp7_sidecar_identity_present(line: str, scoped: Callable[[str], bool]) -> bool:
    if not EXPLICIT_DEVICE_ID_PATTERN.search(line):
        return False
    if not scoped(line):
        return False
    return any(pattern.search(line) for pattern in (ASSIGNMENT_ID_PATTERN, *SESSION_ID_PATTERNS, *LESSON_ID_PATTERNS))

def _cp7_run_identity(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, set[str]]:
    assignment_ids: set[str] = set()
    session_ids: set[str] = set()
    lesson_ids: set[str] = set()
    identity_predicates = (
        _active_assignment_current,
        _manifest_fetched_with_identity,
        _positive_frame("lesson_prepare"),
        _positive_frame("lesson_start"),
        _positive_frame("lesson_step"),
        _robot_lesson_progress_success,
        _backend_progress_posted,
        _sent_frame("lesson_stop"),
        _lesson_stop_received,
        _runtime_lesson_completed,
        _backend_completion_posted,
        _assignment_completed,
        _interactive_child_response_window_opened,
        _interactive_child_response_evidence,
    )
    for line in lines:
        if not scoped(line):
            continue
        if not any(predicate(line) for predicate in identity_predicates):
            continue
        assignment_ids.update(ASSIGNMENT_ID_PATTERN.findall(line))
        session_ids.update(_session_ids_from_evidence(line))
        lesson_ids.update(_ids_from_evidence(line, LESSON_ID_PATTERNS))
    return {
        "assignment_ids": assignment_ids,
        "session_ids": session_ids,
        "lesson_ids": lesson_ids,
    }

def _cp7_sidecar_matches_run_identity(line: str, run_identity: dict[str, set[str]]) -> bool:
    sidecar_assignment_ids = set(ASSIGNMENT_ID_PATTERN.findall(line))
    sidecar_session_ids = _session_ids_from_evidence(line)
    sidecar_lesson_ids = _ids_from_evidence(line, LESSON_ID_PATTERNS)
    required_matches = (
        (run_identity["assignment_ids"], sidecar_assignment_ids),
        (run_identity["session_ids"], sidecar_session_ids),
    )
    for expected, actual in required_matches:
        if expected and not (actual & expected):
            return False
    if run_identity["lesson_ids"] and sidecar_lesson_ids and not (sidecar_lesson_ids & run_identity["lesson_ids"]):
        return False
    return any(
        expected and actual & expected
        for expected, actual in (
            (run_identity["assignment_ids"], sidecar_assignment_ids),
            (run_identity["session_ids"], sidecar_session_ids),
            (run_identity["lesson_ids"], sidecar_lesson_ids),
        )
    )

def _sidecar_evidence_check(
    lines: list[str],
    scoped: Callable[[str], bool],
    run_identity: dict[str, set[str]],
    name: str,
    predicate: Callable[[str], bool],
    missing: str,
) -> dict[str, Any]:
    evidence = next(
        (
            redact_line(line)
            for line in lines
            if _cp7_sidecar_identity_present(line, scoped)
            and _cp7_sidecar_matches_run_identity(line, run_identity)
            and predicate(line)
        ),
        None,
    )
    return _check(name, evidence, missing)

def _assignment_consistency_check(
    checks: list[dict[str, Any]],
    lines: list[str] | None = None,
    scoped: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    assignment_ids: set[str] = set()
    for check in checks:
        evidence = check.get("evidence")
        if not evidence:
            continue
        assignment_ids.update(ASSIGNMENT_ID_PATTERN.findall(str(evidence)))

    if lines is not None and scoped is not None:
        assignment_predicates = (
            _active_assignment_current,
            _manifest_fetched_with_identity,
            _positive_frame("lesson_prepare"),
            _positive_frame("lesson_start"),
            _positive_frame("lesson_step"),
            _robot_lesson_progress_success,
            _backend_progress_posted,
            _sent_frame("lesson_stop"),
            _runtime_lesson_completed,
            _backend_completion_posted,
            _assignment_completed,
            _interactive_child_response_window_opened,
            _interactive_child_response_evidence,
        )
        for line in lines:
            if not scoped(line):
                continue
            if not any(predicate(line) for predicate in assignment_predicates):
                continue
            assignment_ids.update(ASSIGNMENT_ID_PATTERN.findall(line))

    if len(assignment_ids) <= 1:
        evidence = "assignmentIds=" + ",".join(sorted(assignment_ids)) if assignment_ids else "no assignmentId evidence"
        return _check("assignment_consistent", evidence, "")

    evidence = "assignmentIds=" + ",".join(sorted(assignment_ids))
    return {
        "name": "assignment_consistent",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson evidence belongs to multiple assignmentIds",
    }

def _session_ids_from_evidence(evidence: str) -> set[str]:
    session_ids: set[str] = set()
    for pattern in SESSION_ID_PATTERNS:
        session_ids.update(pattern.findall(evidence))
    return session_ids

def _session_consistency_check(
    checks: list[dict[str, Any]],
    lines: list[str] | None = None,
    scoped: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    session_ids: set[str] = set()
    for check in checks:
        evidence = check.get("evidence")
        if not evidence:
            continue
        session_ids.update(_session_ids_from_evidence(str(evidence)))

    if lines is not None and scoped is not None:
        session_predicates = (
            _positive_frame("lesson_prepare"),
            _positive_frame("lesson_start"),
            _positive_frame("lesson_step"),
            _robot_lesson_progress_success,
            _backend_progress_posted,
            _sent_frame("lesson_stop"),
            _lesson_stop_received,
            _runtime_lesson_completed,
            _backend_completion_posted,
            _interactive_child_response_window_opened,
            _interactive_child_response_evidence,
        )
        for line in lines:
            if not scoped(line):
                continue
            if not any(predicate(line) for predicate in session_predicates):
                continue
            session_ids.update(_session_ids_from_evidence(line))

    if len(session_ids) <= 1:
        evidence = "sessionIds=" + ",".join(sorted(session_ids)) if session_ids else "no sessionId evidence"
        return _check("session_consistent", evidence, "")

    evidence = "sessionIds=" + ",".join(sorted(session_ids))
    return {
        "name": "session_consistent",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson evidence belongs to multiple sessionIds",
    }

def _step_ids_from_evidence(evidence: str) -> set[str]:
    step_ids: set[str] = set()
    for pattern in STEP_ID_PATTERNS:
        step_ids.update(pattern.findall(evidence))
    return step_ids

def _ids_from_evidence(evidence: str, patterns: Iterable[re.Pattern[str]]) -> set[str]:
    ids: set[str] = set()
    for pattern in patterns:
        ids.update(pattern.findall(evidence))
    return ids

def _primary_words_from_evidence(evidence: str) -> set[str]:
    return {_norm(word) for word in _ids_from_evidence(evidence, PRIMARY_WORD_PATTERNS)}

def _robot_states_from_evidence(evidence: str) -> set[str]:
    return {_norm(state) for state in _ids_from_evidence(evidence, ROBOT_STATE_PATTERNS)}

def _completion_classes_from_evidence(evidence: str) -> set[str]:
    return {_norm(value) for value in _ids_from_evidence(evidence, COMPLETION_CLASS_PATTERNS)}

def _manifest_checksums_from_evidence(evidence: str) -> set[str]:
    return {_norm(value) for value in _ids_from_evidence(evidence, MANIFEST_CHECKSUM_PATTERNS)}

def _values_by_step(
    lines: list[str],
    scoped: Callable[[str], bool],
    predicate: Callable[[str], bool],
    extractor: Callable[[str], set[str]],
) -> dict[str, set[str]]:
    values_by_step: dict[str, set[str]] = {}
    for line in lines:
        if not scoped(line) or not predicate(line):
            continue
        values = extractor(line)
        if not values:
            continue
        for step_id in _step_ids_from_evidence(line):
            values_by_step.setdefault(step_id, set()).update(values)
    return values_by_step

def _lesson_content_consistency_check(
    checks: list[dict[str, Any]],
    lines: list[str] | None = None,
    scoped: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    lesson_ids: set[str] = set()
    course_ids: set[str] = set()
    content_scoped_checks = {
        "assignment_current",
        "manifest_fetched",
        "lesson_prepare_sent",
        "lesson_start_sent",
        "lesson_step_sent",
        "lesson_completed",
        "assignment_completed",
    }
    for check in checks:
        if check.get("name") not in content_scoped_checks:
            continue
        evidence = check.get("evidence")
        if not evidence:
            continue
        lesson_ids.update(_ids_from_evidence(str(evidence), LESSON_ID_PATTERNS))
        course_ids.update(_ids_from_evidence(str(evidence), COURSE_ID_PATTERNS))

    if lines is not None and scoped is not None:
        identity_predicates = (
            _active_assignment_current,
            _manifest_fetched_with_identity,
            _positive_frame("lesson_prepare"),
            _positive_frame("lesson_start"),
            _positive_frame("lesson_step"),
            _robot_lesson_progress_success,
            _backend_progress_posted,
            _backend_completion_posted,
            _assignment_completed,
        )
        for line in lines:
            if not scoped(line):
                continue
            if not any(predicate(line) for predicate in identity_predicates):
                continue
            lesson_ids.update(_ids_from_evidence(line, LESSON_ID_PATTERNS))
            course_ids.update(_ids_from_evidence(line, COURSE_ID_PATTERNS))

    lesson_evidence = "lessonIds=" + ",".join(sorted(lesson_ids)) if lesson_ids else "lessonIds=none"
    course_evidence = "courseIds=" + ",".join(sorted(course_ids)) if course_ids else "courseIds=none"
    evidence = f"{lesson_evidence}; {course_evidence}"
    if len(lesson_ids) <= 1 and len(course_ids) <= 1:
        return _check("lesson_content_consistent", evidence, "")

    return {
        "name": "lesson_content_consistent",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson evidence belongs to multiple lessonIds/courseIds",
    }

def _expected_lesson_identity_check(
    lines: list[str],
    scoped: Callable[[str], bool],
    expected_lesson_id: str | None,
) -> dict[str, Any]:
    if not expected_lesson_id:
        return _check("expected_lesson_identity", "expected=none; observed=not_required", "")

    expected = _norm(expected_lesson_id)
    observed: set[str] = set()
    assignment_current: set[str] = set()
    identity_predicates = (
        _active_assignment_current,
        _manifest_fetched_with_identity,
            _positive_frame("lesson_prepare"),
            _positive_frame("lesson_start"),
            _positive_frame("lesson_step"),
            _robot_lesson_progress_success,
            _backend_progress_posted,
            _backend_completion_posted,
            _assignment_completed,
        )
    for line in lines:
        if not scoped(line):
            continue
        if _active_assignment_current(line):
            assignment_current.update(
                _norm(lesson_id) for lesson_id in _ids_from_evidence(line, LESSON_ID_PATTERNS)
            )
        if not any(predicate(line) for predicate in identity_predicates):
            continue
        observed.update(_norm(lesson_id) for lesson_id in _ids_from_evidence(line, LESSON_ID_PATTERNS))

    observed_label = ",".join(sorted(observed)) if observed else "none"
    assignment_label = ",".join(sorted(assignment_current)) if assignment_current else "none"
    evidence = f"expected={expected}; observed={observed_label}; assignment_current={assignment_label}"
    if observed == {expected} and assignment_current == {expected}:
        return _check("expected_lesson_identity", evidence, "")

    return {
        "name": "expected_lesson_identity",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson evidence does not match the expected lesson id",
    }

def _expected_course_identity_check(
    lines: list[str],
    scoped: Callable[[str], bool],
    expected_course_id: str | None,
) -> dict[str, Any]:
    if not expected_course_id:
        return _check("expected_course_identity", "expected=none; observed=not_required", "")

    expected = _norm(expected_course_id)
    observed: set[str] = set()
    assignment_current: set[str] = set()
    manifest: set[str] = set()
    identity_predicates = (
        _active_assignment_current,
        _manifest_fetched_with_identity,
            _positive_frame("lesson_prepare"),
            _positive_frame("lesson_start"),
            _positive_frame("lesson_step"),
            _robot_lesson_progress_success,
            _backend_progress_posted,
            _backend_completion_posted,
            _assignment_completed,
        )
    for line in lines:
        if not scoped(line):
            continue
        if _active_assignment_current(line):
            assignment_current.update(
                _norm(course_id) for course_id in _ids_from_evidence(line, COURSE_ID_PATTERNS)
            )
        if _manifest_fetched_with_identity(line):
            manifest.update(
                _norm(course_id) for course_id in _ids_from_evidence(line, COURSE_ID_PATTERNS)
            )
        if not any(predicate(line) for predicate in identity_predicates):
            continue
        observed.update(_norm(course_id) for course_id in _ids_from_evidence(line, COURSE_ID_PATTERNS))

    observed_label = ",".join(sorted(observed)) if observed else "none"
    assignment_label = ",".join(sorted(assignment_current)) if assignment_current else "none"
    manifest_label = ",".join(sorted(manifest)) if manifest else "none"
    evidence = (
        f"expected={expected}; observed={observed_label}; manifest={manifest_label}; "
        f"assignment_current={assignment_label}"
    )
    if observed == {expected} and manifest == {expected} and assignment_current <= {expected}:
        return _check("expected_course_identity", evidence, "")

    return {
        "name": "expected_course_identity",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson evidence does not match the expected course id",
    }

def _normalize_backend_url(value: str) -> str:
    return value.strip().rstrip("/.,;)]}")

def _backend_url_matches_expected(observed: str, expected: str) -> bool:
    return observed == expected or observed.startswith(expected + "/") or observed.startswith(expected + "?")

def _backend_url_evidence_line(line: str) -> bool:
    normalized = line.replace("\\/", "/")
    if BACKEND_URL_FIELD_PATTERN.search(normalized):
        return True
    return any(
        LESSON_API_ENDPOINT_PATTERN.match(url)
        for url in HTTP_URL_PATTERN.findall(normalized)
    )


def _backend_urls_from_evidence_line(line: str) -> set[str]:
    normalized = line.replace("\\/", "/")
    explicit = {
        _normalize_backend_url(match.group(1))
        for match in BACKEND_URL_FIELD_PATTERN.finditer(normalized)
    }
    endpoints = {
        _normalize_backend_url(url)
        for url in HTTP_URL_PATTERN.findall(normalized)
        if LESSON_API_ENDPOINT_PATTERN.match(url)
    }
    return explicit | endpoints

def _expected_backend_url_check(
    lines: list[str],
    scoped: Callable[[str], bool],
    expected_backend_url: str | None,
) -> dict[str, Any]:
    if not expected_backend_url:
        return _check("expected_backend_url", "expected=none; observed=not_required", "")

    expected = _normalize_backend_url(expected_backend_url)
    observed: set[str] = set()
    for line in lines:
        if not scoped(line) or not _backend_url_evidence_line(line):
            continue
        observed.update(_backend_urls_from_evidence_line(line))

    observed_label = ",".join(sorted(observed)) if observed else "none"
    evidence = f"expected={expected}; observed={observed_label}"
    if any(_backend_url_matches_expected(candidate, expected) for candidate in observed):
        return _check("expected_backend_url", evidence, "")

    return {
        "name": "expected_backend_url",
        "ok": False,
        "evidence": evidence,
        "missing": "captured lesson evidence does not mention the expected backend URL",
    }

def _lesson_version_present_check(
    lines: list[str], scoped: Callable[[str], bool], required: bool = False
) -> dict[str, Any]:
    assignment_lines = [line for line in lines if scoped(line) and _active_assignment_current(line)]
    if not assignment_lines:
        return _check("lesson_version_present", "no assignment_current evidence; lessonVersion check skipped", "")
    versions: set[str] = set()
    for line in assignment_lines:
        versions.update(_ids_from_evidence(line, LESSON_VERSION_PATTERNS))
    evidence = "lessonVersion=" + ",".join(sorted(versions)) if versions else "lessonVersion=none"
    if versions:
        return _check("lesson_version_present", evidence, "")
    if not required:
        # lessonVersion is mandatory only for strict assignment captures (opt-in via
        # --require-lesson-version). Legacy fixtures predate the field, so the
        # gate is advisory unless the operator demands the pin be present.
        return _check("lesson_version_present", evidence + "; not_required", "")
    return {
        "name": "lesson_version_present",
        "ok": False,
        "evidence": evidence,
        "missing": "assignment/current evidence must include lessonVersion",
    }

def _assignment_version_present_check(
    lines: list[str], scoped: Callable[[str], bool], required: bool = False
) -> dict[str, Any]:
    assignment_lines = [line for line in lines if scoped(line) and _active_assignment_current(line)]
    if not assignment_lines:
        return _check(
            "assignment_version_present",
            "no assignment_current evidence; assignmentVersion check skipped",
            "",
        )
    versions: set[str] = set()
    for line in assignment_lines:
        versions.update(_ids_from_evidence(line, ASSIGNMENT_VERSION_PATTERNS))
    evidence = "assignmentVersion=" + ",".join(sorted(versions)) if versions else "assignmentVersion=none"
    if versions:
        return _check("assignment_version_present", evidence, "")
    if not required:
        return _check("assignment_version_present", evidence + "; not_required", "")
    return {
        "name": "assignment_version_present",
        "ok": False,
        "evidence": evidence,
        "missing": "assignment/current evidence must include assignmentVersion",
    }

def _lesson_story_present_check(
    lines: list[str], scoped: Callable[[str], bool], required: bool = False
) -> dict[str, Any]:
    lesson_step_frame = _positive_frame("lesson_step")

    def story_evidence_line(line: str) -> bool:
        if not any(pattern.search(line) for pattern in STORY_EVIDENCE_PATTERNS):
            return False
        return _manifest_fetched_with_identity(line) or lesson_step_frame(line)

    story_lines = [
        line
        for line in lines
        if scoped(line) and story_evidence_line(line)
    ]
    wait_story_lines = [
        line
        for line in story_lines
        if any(pattern.search(line) for pattern in STORY_WAIT_FOR_CHILD_PATTERNS)
    ]
    missing_wait_step_ids: set[str] = set()
    missing_guided_question_step_ids: set[str] = set()
    if required:
        for line in story_lines:
            if lesson_step_frame(line):
                if any(pattern.search(line) for pattern in STORY_WAIT_FOR_CHILD_PATTERNS) and not _story_line_guided_question(line):
                    missing_guided_question_step_ids.update(_step_ids_from_evidence(line))
                continue
            if not _manifest_fetched_with_identity(line):
                continue
            completion_classes = _manifest_steps_array_completion_classes(line)
            wait_for_child = _manifest_steps_array_wait_for_child(line)
            guided_questions = _manifest_steps_array_guided_questions(line)
            if completion_classes is None or wait_for_child is None or guided_questions is None:
                continue
            missing_wait_step_ids.update(
                step_id
                for step_id, completion_class in completion_classes.items()
                if completion_class == "interactive" and wait_for_child.get(step_id) is not True
            )
            missing_guided_question_step_ids.update(
                step_id
                for step_id, completion_class in completion_classes.items()
                if completion_class == "interactive" and guided_questions.get(step_id) is not True
            )
    if missing_guided_question_step_ids:
        return {
            "name": "lesson_story_present",
            "ok": False,
            "evidence": "missing_guided_question="
            + ",".join(sorted(missing_guided_question_step_ids)),
            "missing": "required interactive story evidence must include a guided child-facing question",
        }
    if missing_wait_step_ids:
        return {
            "name": "lesson_story_present",
            "ok": False,
            "evidence": "missing_waitForChild=" + ",".join(sorted(missing_wait_step_ids)),
            "missing": "required interactive manifest steps must include storyBeat.waitForChild=true",
        }
    if required and story_lines and not wait_story_lines:
        return {
            "name": "lesson_story_present",
            "ok": False,
            "evidence": redact_line(story_lines[0].strip()),
            "missing": "required lesson story/storyBeat evidence must include waitForChild=true",
        }
    if story_lines:
        evidence_line = wait_story_lines[0] if wait_story_lines else story_lines[0]
        return _check("lesson_story_present", redact_line(evidence_line.strip()), "")
    evidence = "story=none"
    if not required:
        return _check("lesson_story_present", evidence + "; not_required", "")
    return {
        "name": "lesson_story_present",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson manifest or lesson_step evidence must include story/storyBeat/narrative metadata",
    }

def _expected_child_identity_check(
    lines: list[str], scoped: Callable[[str], bool], expected_child_id: str | None
) -> dict[str, Any]:
    if not expected_child_id:
        return _check("expected_child_identity", "expected=none; observed=not_required", "")
    expected = _norm(expected_child_id)
    observed: set[str] = set()
    assignment_current: set[str] = set()
    for line in lines:
        if not scoped(line):
            continue
        child_ids = {_norm(child_id) for child_id in _ids_from_evidence(line, CHILD_ID_PATTERNS)}
        if _active_assignment_current(line):
            assignment_current.update(child_ids)
        observed.update(child_ids)
    observed_label = ",".join(sorted(observed)) if observed else "none"
    assignment_label = ",".join(sorted(assignment_current)) if assignment_current else "none"
    evidence = f"expected={expected}; observed={observed_label}; assignment_current={assignment_label}"
    if assignment_current == {expected}:
        return _check("expected_child_identity", evidence, "")
    return {
        "name": "expected_child_identity",
        "ok": False,
        "evidence": evidence,
        "missing": "assignment/current childId must match expected child id",
    }

def _expected_device_binding_check(
    lines: list[str], scoped: Callable[[str], bool], expected_device_binding: str | None
) -> dict[str, Any]:
    if not expected_device_binding:
        return _check("expected_device_binding", "expected=none; observed=not_required", "")
    expected = _norm(expected_device_binding)
    observed: set[str] = set()
    assignment_current: set[str] = set()
    for line in lines:
        if not scoped(line):
            continue
        device_ids = {_norm(match.group(1)) for match in BACKEND_DEVICE_ID_PATTERN.finditer(line)}
        if _active_assignment_current(line):
            assignment_current.update(device_ids)
        observed.update(device_ids)
    observed_label = ",".join(sorted(observed)) if observed else "none"
    assignment_label = ",".join(sorted(assignment_current)) if assignment_current else "none"
    evidence = f"expected={expected}; observed={observed_label}; assignment_current={assignment_label}"
    if assignment_current == {expected}:
        return _check("expected_device_binding", evidence, "")
    return {
        "name": "expected_device_binding",
        "ok": False,
        "evidence": evidence,
        "missing": "assignment/current backendDeviceId must match expected backend device UUID",
    }

def _manifest_profile_esp_tft_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    profiles: set[str] = set()
    for line in lines:
        if not scoped(line) or not _manifest_fetched_with_identity(line):
            continue
        profiles.update(_norm(profile) for profile in _ids_from_evidence(line, PROFILE_PATTERNS))

    evidence = "profiles=" + ",".join(sorted(profiles)) if profiles else "profiles=none"
    if profiles == {"esptft"}:
        return _check("manifest_profile_esp_tft", evidence, "")

    return {
        "name": "manifest_profile_esp_tft",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson manifest must be fetched for the espTft robot profile",
    }

def _line_assignment_ids(line: str) -> set[str]:
    return {_norm(value) for value in ASSIGNMENT_ID_PATTERN.findall(line)}


def _checksum_pair_label(
    assignment_id: str,
    session_id: str | None,
    left_label: str,
    left: set[str],
    right_label: str,
    right: set[str],
) -> str:
    identity = assignment_id + (f"/{session_id}" if session_id else "")
    return (
        f"{identity}:{left_label}={','.join(sorted(left))};"
        f"{right_label}={','.join(sorted(right))}"
    )


def _assignment_manifest_checksum_pairs(
    lines: list[str], scoped: Callable[[str], bool]
) -> tuple[set[str], set[str], list[str], list[str]]:
    assignment_checksums: set[str] = set()
    manifest_checksums: set[str] = set()
    current_by_identity: dict[tuple[str, str | None], set[str]] = {}
    sessionless_candidates: dict[str, list[set[str]]] = {}
    bound_by_identity: dict[tuple[str, str], set[str]] = {}
    latest_by_assignment: dict[str, set[str]] = {}
    pairs: list[str] = []
    mismatches: list[str] = []
    for line in lines:
        if not scoped(line):
            continue
        assignment_ids = _line_assignment_ids(line)
        session_ids = sorted(_session_ids_from_evidence(line))
        session_id = _norm(session_ids[0]) if len(session_ids) == 1 else None
        checksums = _manifest_checksums_from_evidence(line)
        if _active_assignment_current(line):
            assignment_checksums.update(checksums)
            for assignment_id in assignment_ids:
                if session_id is None:
                    sessionless_candidates.setdefault(assignment_id, []).append(set(checksums))
                else:
                    current_by_identity[(assignment_id, session_id)] = set(checksums)
                latest_by_assignment[assignment_id] = set(checksums)
        if not _manifest_fetched_with_identity(line):
            continue
        manifest_checksums.update(checksums)
        for assignment_id in assignment_ids:
            expected = current_by_identity.get((assignment_id, session_id))
            if expected is None and session_id is not None:
                expected = bound_by_identity.get((assignment_id, session_id))
            if expected is None:
                candidates = sessionless_candidates.get(assignment_id, [])
                expected = candidates[-1] if candidates else None
            if expected is None:
                expected = latest_by_assignment.get(assignment_id, set())
            if not expected or not checksums:
                continue
            if session_id is not None:
                bound_by_identity.setdefault((assignment_id, session_id), set(expected))
            label = _checksum_pair_label(
                assignment_id, session_id, "assignment", expected, "manifest", checksums
            )
            pairs.append(label)
            if expected != checksums:
                mismatches.append(label)
    return assignment_checksums, manifest_checksums, pairs, mismatches


def _manifest_prepare_checksum_pairs(
    lines: list[str], scoped: Callable[[str], bool]
) -> tuple[set[str], set[str], list[str], list[str]]:
    manifest_checksums: set[str] = set()
    prepare_checksums: set[str] = set()
    manifest_by_identity: dict[tuple[str, str | None], set[str]] = {}
    latest_manifest_by_assignment: dict[str, set[str]] = {}
    pairs: list[str] = []
    mismatches: list[str] = []
    prepare_predicate = _positive_frame("lesson_prepare")
    for line in lines:
        if not scoped(line):
            continue
        assignment_ids = _line_assignment_ids(line)
        session_ids = sorted(_session_ids_from_evidence(line))
        session_id = _norm(session_ids[0]) if len(session_ids) == 1 else None
        checksums = _manifest_checksums_from_evidence(line)
        if _manifest_fetched_with_identity(line):
            manifest_checksums.update(checksums)
            for assignment_id in assignment_ids:
                manifest_by_identity[(assignment_id, session_id)] = set(checksums)
                latest_manifest_by_assignment[assignment_id] = set(checksums)
        if not prepare_predicate(line):
            continue
        prepare_checksums.update(checksums)
        for assignment_id in assignment_ids:
            expected = manifest_by_identity.get((assignment_id, session_id))
            if expected is None:
                expected = manifest_by_identity.get((assignment_id, None))
            if expected is None:
                expected = latest_manifest_by_assignment.get(assignment_id, set())
            if not expected or not checksums:
                continue
            label = _checksum_pair_label(
                assignment_id, session_id, "manifest", expected, "prepare", checksums
            )
            pairs.append(label)
            if expected != checksums:
                mismatches.append(label)
    return manifest_checksums, prepare_checksums, pairs, mismatches


def _lesson_manifest_checksum_consistency_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    manifest_checksums, prepare_checksums, pairs, mismatches = _manifest_prepare_checksum_pairs(
        lines, scoped
    )

    evidence = "; ".join(
        (
            "manifest=" + ",".join(sorted(manifest_checksums)) if manifest_checksums else "manifest=none",
            "prepare=" + ",".join(sorted(prepare_checksums)) if prepare_checksums else "prepare=none",
            "pairs=" + ("|".join(pairs) if pairs else "none"),
        )
    )
    if not mismatches:
        return _check("lesson_manifest_checksum_consistent", evidence, "")

    return {
        "name": "lesson_manifest_checksum_consistent",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson_prepare manifestRef checksum must match fetched manifest checksum",
    }

def _assignment_manifest_checksum_consistency_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    assignment_checksums, manifest_checksums, pairs, mismatches = (
        _assignment_manifest_checksum_pairs(lines, scoped)
    )

    evidence = "; ".join(
        (
            "assignment=" + ",".join(sorted(assignment_checksums)) if assignment_checksums else "assignment=none",
            "manifest=" + ",".join(sorted(manifest_checksums)) if manifest_checksums else "manifest=none",
            "pairs=" + ("|".join(pairs) if pairs else "none"),
        )
    )
    if not mismatches:
        return _check("lesson_assignment_manifest_checksum_consistent", evidence, "")

    return {
        "name": "lesson_assignment_manifest_checksum_consistent",
        "ok": False,
        "evidence": evidence,
        "missing": "fetched manifest checksum must match current assignment manifestChecksum",
    }

def _manifest_pin_blocks_frames_on_mismatch_check(
    lines: list[str], scoped: Callable[[str], bool]
) -> dict[str, Any]:
    """Manifest-pin negative path validator.

    The consistency gates above fail the run when a started lesson carries a
    checksum that disagrees with the fetched manifest. This gate validates the
    *desired* clean-abort behaviour instead: when the assignment checksum and
    the fetched manifest checksum disagree (or an explicit manifest-pin mismatch
    marker is logged), the runtime must (a) surface a start-status error and
    (b) send NO lesson_prepare/lesson_start/lesson_step frames. It accepts a
    capture that proves that shape and rejects one where a mismatch still emitted
    frames or was swallowed without a surfaced error. When no mismatch is present
    the gate is not applicable and passes.
    """
    _, _, checksum_pairs, checksum_mismatches = _assignment_manifest_checksum_pairs(
        lines, scoped
    )
    explicit_mismatch = False
    start_status_error = False
    frame_predicates = (
        ("lesson_prepare", _positive_frame("lesson_prepare")),
        ("lesson_start", _positive_frame("lesson_start")),
        ("lesson_step", _positive_frame("lesson_step")),
    )
    frames_sent: list[str] = []

    for line in lines:
        if not scoped(line):
            continue
        if any(pattern.search(line) for pattern in MANIFEST_PIN_MISMATCH_PATTERNS):
            explicit_mismatch = True
        if any(pattern.search(line) for pattern in MANIFEST_PIN_START_STATUS_ERROR_PATTERNS):
            start_status_error = True
        for label, predicate in frame_predicates:
            if predicate(line) and label not in frames_sent:
                frames_sent.append(label)

    checksum_mismatch = bool(checksum_mismatches)
    mismatch_present = checksum_mismatch or explicit_mismatch

    if checksum_mismatch:
        mismatch_label = "|".join(checksum_mismatches)
    elif explicit_mismatch:
        mismatch_label = "explicit_marker"
    else:
        mismatch_label = "none"

    frames_label = ",".join(frames_sent) if frames_sent else "none"
    evidence = "; ".join(
        (
            "mismatch=" + mismatch_label,
            "no_mismatch" if not mismatch_present else "mismatch_present",
            "start_status_error=" + ("present" if start_status_error else "none"),
            "frames=" + frames_label,
            "pairs=" + ("|".join(checksum_pairs) if checksum_pairs else "none"),
        )
    )

    if not mismatch_present:
        return _check("lesson_manifest_pin_blocks_frames_on_mismatch", evidence, "")

    if start_status_error and not frames_sent:
        return _check("lesson_manifest_pin_blocks_frames_on_mismatch", evidence, "")

    return {
        "name": "lesson_manifest_pin_blocks_frames_on_mismatch",
        "ok": False,
        "evidence": evidence,
        "missing": "manifest checksum mismatch must surface a start-status error and send no lesson_prepare/lesson_start/lesson_step frames",
    }


def evaluate_manifest_pin_abort_logs(
    lines,
    *,
    device_id: str = DEFAULT_DEVICE_ID,
    device_aliases=None,
):
    """Validate a bounded assignment-vs-manifest pin rejection capture.

    This is intentionally separate from ``evaluate_lesson_logs``: a clean pin
    rejection must not contain the successful prepare/start/step/completion flow
    required by the normal lesson scenario.
    """
    materialized = [line.rstrip("\n") for line in lines]
    aliases = list(device_aliases or [])
    scoped = _device_scope(device_id, aliases)
    gate = _manifest_pin_blocks_frames_on_mismatch_check(materialized, scoped)
    _, _, _, checksum_mismatches = _assignment_manifest_checksum_pairs(
        materialized, scoped
    )
    explicit_mismatch = any(
        scoped(line)
        and any(pattern.search(line) for pattern in MANIFEST_PIN_MISMATCH_PATTERNS)
        for line in materialized
    )
    mismatch_evidence = (
        "paired=" + "|".join(checksum_mismatches)
        if checksum_mismatches
        else "explicit_marker"
        if explicit_mismatch
        else None
    )
    checks = [
        _check(
            "manifest_pin_mismatch_present",
            mismatch_evidence,
            "no assignment-vs-manifest or explicit start-pin mismatch evidence",
        ),
        gate,
    ]
    return {
        "ok": all(check["ok"] for check in checks),
        "device_id": device_id,
        "device_aliases": aliases,
        "line_count": len(materialized),
        "checks": checks,
    }

def _teaching_object_primary_word_consistency_check(
    checks: list[dict[str, Any]],
    lines: list[str],
    scoped: Callable[[str], bool],
) -> dict[str, Any]:
    step_words_by_step = _values_by_step(lines, scoped, _positive_frame("lesson_step"), _primary_words_from_evidence)
    rendered_words_by_step = _values_by_step(lines, scoped, _lesson_content_rendered, _primary_words_from_evidence)
    content_step_ids = _step_ids_matching(lines, scoped, _lesson_content_rendered)
    compared_steps = sorted(set(step_words_by_step) | set(rendered_words_by_step))
    mismatched_steps = [
        step_id
        for step_id in compared_steps
        if step_words_by_step.get(step_id)
        and rendered_words_by_step.get(step_id)
        and not rendered_words_by_step[step_id].issubset(step_words_by_step[step_id])
    ]
    missing_rendered_primary_word = sorted(
        step_id for step_id in content_step_ids if step_words_by_step.get(step_id) and not rendered_words_by_step.get(step_id)
    )
    if len(compared_steps) > 1 and mismatched_steps or missing_rendered_primary_word:
        evidence = "; ".join(
            (
                "stepPrimaryWords="
                + ",".join(
                    f"{step}:{'/'.join(sorted(words))}" for step, words in sorted(step_words_by_step.items())
                ),
                "renderedPrimaryWords="
                + ",".join(
                    f"{step}:{'/'.join(sorted(words))}" for step, words in sorted(rendered_words_by_step.items())
                ),
                "mismatch=" + ",".join(mismatched_steps),
                "missing_rendered_primary_word="
                + (",".join(missing_rendered_primary_word) if missing_rendered_primary_word else "none"),
            )
        )
        return {
            "name": "teaching_object_primary_word_consistent",
            "ok": False,
            "evidence": evidence,
            "missing": "rendered teachingObject primaryWord does not match lesson_step primaryWord for the same stepId",
        }

    step_words: set[str] = set()
    rendered_words: set[str] = set()
    for check in checks:
        evidence = check.get("evidence")
        if not evidence:
            continue
        if check.get("name") == "lesson_step_sent":
            step_words.update(_primary_words_from_evidence(str(evidence)))
        elif check.get("name") == "lesson_content_rendered":
            rendered_words.update(_primary_words_from_evidence(str(evidence)))

    step_evidence = "stepPrimaryWords=" + ",".join(sorted(step_words)) if step_words else "stepPrimaryWords=none"
    render_evidence = "renderedPrimaryWords=" + ",".join(sorted(rendered_words)) if rendered_words else "renderedPrimaryWords=none"
    evidence = f"{step_evidence}; {render_evidence}"
    if not step_words or not rendered_words or rendered_words.issubset(step_words):
        return _check("teaching_object_primary_word_consistent", evidence, "")

    return {
        "name": "teaching_object_primary_word_consistent",
        "ok": False,
        "evidence": evidence,
        "missing": "rendered teachingObject primaryWord does not match lesson_step primaryWord",
    }

def _robot_overlay_state_consistency_check(
    checks: list[dict[str, Any]],
    lines: list[str],
    scoped: Callable[[str], bool],
) -> dict[str, Any]:
    step_states_by_step = _values_by_step(lines, scoped, _positive_frame("lesson_step"), _robot_states_from_evidence)
    overlay_states_by_step = _values_by_step(lines, scoped, _robot_overlay_rendered, _robot_states_from_evidence)
    overlay_step_ids = _step_ids_matching(lines, scoped, _robot_overlay_rendered)
    rendered_states_by_step: dict[str, set[str]] = {}
    for line in lines:
        if not scoped(line) or not (_robot_overlay_rendered(line) or _lesson_step_rendered_ack(line)):
            continue
        states = _robot_states_from_evidence(line)
        if not states:
            continue
        for step_id in _step_ids_from_evidence(line):
            rendered_states_by_step.setdefault(step_id, set()).update(states)
    compared_steps = sorted(set(step_states_by_step) | set(rendered_states_by_step))
    mismatched_steps = [
        step_id
        for step_id in compared_steps
        if step_states_by_step.get(step_id)
        and rendered_states_by_step.get(step_id)
        and not rendered_states_by_step[step_id].issubset(step_states_by_step[step_id])
    ]
    missing_rendered_robot_state = sorted(
        step_id for step_id in overlay_step_ids if step_states_by_step.get(step_id) and not overlay_states_by_step.get(step_id)
    )
    if len(compared_steps) > 1 and mismatched_steps or missing_rendered_robot_state:
        evidence = "; ".join(
            (
                "stepRobotStates="
                + ",".join(
                    f"{step}:{'/'.join(sorted(states))}" for step, states in sorted(step_states_by_step.items())
                ),
                "renderedRobotStates="
                + ",".join(
                    f"{step}:{'/'.join(sorted(states))}"
                    for step, states in sorted(rendered_states_by_step.items())
                ),
                "mismatch=" + ",".join(mismatched_steps),
                "missing_rendered_robot_state="
                + (",".join(missing_rendered_robot_state) if missing_rendered_robot_state else "none"),
            )
        )
        return {
            "name": "robot_overlay_state_consistent",
            "ok": False,
            "evidence": evidence,
            "missing": "rendered robotOverlay/ack robotState does not match lesson_step robotState for the same stepId",
        }

    step_states: set[str] = set()
    rendered_states: set[str] = set()
    for check in checks:
        evidence = check.get("evidence")
        if not evidence:
            continue
        if check.get("name") == "lesson_step_sent":
            step_states.update(_robot_states_from_evidence(str(evidence)))
        elif check.get("name") in {"robot_overlay_rendered", "lesson_step_ack"}:
            rendered_states.update(_robot_states_from_evidence(str(evidence)))

    step_evidence = "stepRobotStates=" + ",".join(sorted(step_states)) if step_states else "stepRobotStates=none"
    render_evidence = "renderedRobotStates=" + ",".join(sorted(rendered_states)) if rendered_states else "renderedRobotStates=none"
    evidence = f"{step_evidence}; {render_evidence}"
    if not step_states or not rendered_states or rendered_states.issubset(step_states):
        return _check("robot_overlay_state_consistent", evidence, "")

    return {
        "name": "robot_overlay_state_consistent",
        "ok": False,
        "evidence": evidence,
        "missing": "rendered robotOverlay/ack robotState does not match lesson_step robotState",
    }

def _lesson_audio_primary_word_consistency_check(
    checks: list[dict[str, Any]],
    lines: list[str],
    scoped: Callable[[str], bool],
) -> dict[str, Any]:
    step_words_by_step = _values_by_step(lines, scoped, _positive_frame("lesson_step"), _primary_words_from_evidence)
    audio_words_by_step = _values_by_step(lines, scoped, _lesson_audio_played, _primary_words_from_evidence)
    audio_step_ids = _step_ids_matching(lines, scoped, _lesson_audio_played)
    compared_steps = sorted(set(step_words_by_step) | set(audio_words_by_step))
    mismatched_steps = [
        step_id
        for step_id in compared_steps
        if step_words_by_step.get(step_id)
        and audio_words_by_step.get(step_id)
        and not audio_words_by_step[step_id].issubset(step_words_by_step[step_id])
    ]
    missing_audio_primary_word = sorted(
        step_id for step_id in audio_step_ids if step_words_by_step.get(step_id) and not audio_words_by_step.get(step_id)
    )
    if len(compared_steps) > 1 and mismatched_steps or missing_audio_primary_word:
        evidence = "; ".join(
            (
                "stepPrimaryWords="
                + ",".join(
                    f"{step}:{'/'.join(sorted(words))}" for step, words in sorted(step_words_by_step.items())
                ),
                "audioPrimaryWords="
                + ",".join(
                    f"{step}:{'/'.join(sorted(words))}" for step, words in sorted(audio_words_by_step.items())
                ),
                "mismatch=" + ",".join(mismatched_steps),
                "missing_audio_primary_word="
                + (",".join(missing_audio_primary_word) if missing_audio_primary_word else "none"),
            )
        )
        return {
            "name": "lesson_audio_primary_word_consistent",
            "ok": False,
            "evidence": evidence,
            "missing": "lesson audio primaryWord does not match lesson_step primaryWord for the same stepId",
        }

    step_words: set[str] = set()
    audio_words: set[str] = set()
    for check in checks:
        evidence = check.get("evidence")
        if not evidence:
            continue
        if check.get("name") == "lesson_step_sent":
            step_words.update(_primary_words_from_evidence(str(evidence)))
        elif check.get("name") == "lesson_audio_played":
            audio_words.update(_primary_words_from_evidence(str(evidence)))

    step_evidence = "stepPrimaryWords=" + ",".join(sorted(step_words)) if step_words else "stepPrimaryWords=none"
    audio_evidence = "audioPrimaryWords=" + ",".join(sorted(audio_words)) if audio_words else "audioPrimaryWords=none"
    evidence = f"{step_evidence}; {audio_evidence}"
    if not step_words or not audio_words or audio_words.issubset(step_words):
        return _check("lesson_audio_primary_word_consistent", evidence, "")

    return {
        "name": "lesson_audio_primary_word_consistent",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson audio primaryWord does not match lesson_step primaryWord",
    }

def _media_urls_from_evidence(evidence: str) -> set[str]:
    return {url.rstrip(",.;)") for url in MEDIA_URL_PATTERN.findall(evidence)}

def _media_urls_by_step(
    lines: list[str],
    scoped: Callable[[str], bool],
    predicate: Callable[[str], bool],
) -> dict[str, set[str]]:
    urls_by_step: dict[str, set[str]] = {}
    for line in lines:
        if not scoped(line) or not predicate(line):
            continue
        urls = _media_urls_from_evidence(line)
        if not urls:
            continue
        for step_id in _step_ids_from_evidence(line):
            urls_by_step.setdefault(step_id, set()).update(urls)
    return urls_by_step

def _background_rendered_with_media(line: str) -> bool:
    return _background_rendered(line) and bool(_media_urls_from_evidence(line))

CHILD_RESPONSE_INPUT_FIELDS = (
    "recognizedText",
    "recognized_text",
    "childResponse",
    "child_response",
    "utterance",
    "transcript",
    "choiceId",
    "choice_id",
    "tapTargetHit",
    "tap_target_hit",
)

EMPTY_CHILD_RESPONSE_VALUES = {
    "",
    "null",
    "none",
    "false",
    "0",
    "[]",
    "{}",
    "unknown",
    "unrecognized",
    "noise",
    "[noise]",
    "inaudible",
    "[inaudible]",
    "silence",
    "no_speech",
    "no-speech",
    "...",
    "<unk>",
    "unk",
    "n/a",
    "na",
}

FALSE_CHILD_RESPONSE_FIELD_VALUES = {"false", "0"}

def _field_value_pattern(field_name: str, *, allow_space_separator: bool = False, allow_empty: bool = False) -> re.Pattern[str]:
    separator = r"(?::|=|\s+)" if allow_space_separator else r"(?::|=)"
    unquoted_value = r"([^,\s}}]*)" if allow_empty else r"([^,\s}}]+)"
    return re.compile(
        rf"(?i)(?<![A-Za-z0-9_.-])(?:[\"']?{re.escape(field_name)}[\"']?)"
        rf"\s*{separator}\s*(?:\"([^\"]*)\"|'([^']*)'|{unquoted_value})"
    )

def _field_values(line: str, field_names: Iterable[str], *, allow_space_separator: bool = False, allow_empty: bool = False) -> Iterable[str]:
    for field_name in field_names:
        pattern = _field_value_pattern(
            field_name,
            allow_space_separator=allow_space_separator,
            allow_empty=allow_empty,
        )
        for match in pattern.finditer(line):
            yield next((group for group in match.groups() if group is not None), "").strip()

def _normalized_child_response_value(value: str) -> str:
    return value.lower().strip().strip(".,;:!?")

def _has_non_empty_field_value(line: str, field_names: Iterable[str]) -> bool:
    for value in _field_values(line, field_names):
        normalized_value = _normalized_child_response_value(value)
        if normalized_value not in EMPTY_CHILD_RESPONSE_VALUES and any(char.isalnum() for char in normalized_value):
            return True
    return False

def _has_invalid_confidence_field(line: str) -> bool:
    for raw_value in _field_values(line, ("confidence", "asrConfidence", "asr_confidence"), allow_empty=True):
        try:
            confidence = float(raw_value)
            if not math.isfinite(confidence) or confidence <= 0.0:
                return True
        except ValueError:
            return True
    return False

def _has_negative_child_response_flag(line: str) -> bool:
    for raw_value in _field_values(
        line,
        ("accepted", "handled", "recognized"),
        allow_space_separator=True,
    ):
        if _normalized_child_response_value(raw_value) in FALSE_CHILD_RESPONSE_FIELD_VALUES:
            return True
    for raw_value in _field_values(line, ("rejected",), allow_space_separator=True):
        if _normalized_child_response_value(raw_value) in {"true", "1"}:
            return True
    return False

def _interactive_child_response_has_observable_input(line: str) -> bool:
    return _has_non_empty_field_value(line, CHILD_RESPONSE_INPUT_FIELDS)

def _interactive_child_response_evidence(line: str) -> bool:
    lowered = _norm(line)
    if any(
        token in lowered
        for token in (
            "child response inactive",
            "child_inactive",
            "no answer",
            "timeout",
            "failed",
            "failure",
            "error",
            "ignored",
            "accepted=false",
            '"accepted":false',
            "handled=false",
            '"handled":false',
            "recognized=false",
            '"recognized":false',
            "recognized=0",
            '"recognized":0',
        )
    ):
        return False
    if _has_negative_child_response_flag(line):
        return False
    if _has_invalid_confidence_field(line):
        return False
    if "interactive child response accepted" in lowered:
        return _interactive_child_response_has_observable_input(line)
    if "child response accepted" in lowered or "lesson child response accepted" in lowered:
        return _interactive_child_response_has_observable_input(line)
    if not _lesson_progress_success(line):
        return False
    return _interactive_child_response_has_observable_input(line)

def _render_media_consistency_check(
    checks: list[dict[str, Any]],
    lines: list[str],
    scoped: Callable[[str], bool],
) -> dict[str, Any]:
    step_urls_by_step = _media_urls_by_step(lines, scoped, _positive_frame("lesson_step"))
    render_urls_by_step = _media_urls_by_step(lines, scoped, _background_rendered)
    mismatched_steps = [
        step_id
        for step_id, render_urls in sorted(render_urls_by_step.items())
        if step_urls_by_step.get(step_id) and not render_urls.issubset(step_urls_by_step[step_id])
    ]
    if mismatched_steps:
        evidence = "; ".join(
            (
                "stepMedia="
                + ",".join(
                    f"{step}:{'/'.join(sorted(urls))}" for step, urls in sorted(step_urls_by_step.items())
                ),
                "renderMedia="
                + ",".join(
                    f"{step}:{'/'.join(sorted(urls))}" for step, urls in sorted(render_urls_by_step.items())
                ),
                "mismatch=" + ",".join(mismatched_steps),
            )
        )
        return {
            "name": "render_media_consistent",
            "ok": False,
            "evidence": evidence,
            "missing": "rendered background/video media does not match lesson_step media for the same stepId",
        }

    step_urls: set[str] = set()
    render_urls: set[str] = set()
    for check in checks:
        evidence = check.get("evidence")
        if not evidence:
            continue
        if check.get("name") == "lesson_step_sent":
            step_urls.update(_media_urls_from_evidence(str(evidence)))
        elif check.get("name") == "background_rendered":
            render_urls.update(_media_urls_from_evidence(str(evidence)))

    step_evidence = "stepMedia=" + ",".join(sorted(step_urls)) if step_urls else "stepMedia=none"
    render_evidence = "renderMedia=" + ",".join(sorted(render_urls)) if render_urls else "renderMedia=none"
    evidence = f"{step_evidence}; {render_evidence}"
    if not step_urls or not render_urls or render_urls.issubset(step_urls):
        return _check("render_media_consistent", evidence, "")

    return {
        "name": "render_media_consistent",
        "ok": False,
        "evidence": evidence,
        "missing": "rendered background/video media does not match lesson_step media",
    }

def _lesson_step_media_declared_check(checks: list[dict[str, Any]]) -> dict[str, Any]:
    step_urls: set[str] = set()
    for check in checks:
        if check.get("name") != "lesson_step_sent":
            continue
        evidence = check.get("evidence")
        if evidence:
            step_urls.update(_media_urls_from_evidence(str(evidence)))

    evidence = "stepMedia=" + ",".join(sorted(step_urls)) if step_urls else "stepMedia=none"
    if step_urls:
        return _check("lesson_step_media_declared", evidence, "")
    return {
        "name": "lesson_step_media_declared",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson_step frame does not declare a poster/video media URL",
    }

def _background_render_media_declared_check(checks: list[dict[str, Any]]) -> dict[str, Any]:
    render_urls: set[str] = set()
    for check in checks:
        if check.get("name") != "background_rendered":
            continue
        evidence = check.get("evidence")
        if evidence:
            render_urls.update(_media_urls_from_evidence(str(evidence)))

    evidence = "renderMedia=" + ",".join(sorted(render_urls)) if render_urls else "renderMedia=none"
    if render_urls:
        return _check("background_render_media_declared", evidence, "")
    return {
        "name": "background_render_media_declared",
        "ok": False,
        "evidence": evidence,
        "missing": "firmware background/poster/video render evidence does not include the rendered media URL",
    }

def _step_consistency_check(checks: list[dict[str, Any]]) -> dict[str, Any]:
    step_ids: set[str] = set()
    step_scoped_checks = {
        "lesson_step_sent",
        "background_rendered",
        "lesson_audio_played",
        "lesson_step_ack",
        "lesson_progress",
    }
    for check in checks:
        if check.get("name") not in step_scoped_checks:
            continue
        evidence = check.get("evidence")
        if not evidence:
            continue
        step_ids.update(_step_ids_from_evidence(str(evidence)))

    if len(step_ids) <= 1:
        evidence = "stepIds=" + ",".join(sorted(step_ids)) if step_ids else "no stepId evidence"
        return _check("step_consistent", evidence, "")

    evidence = "stepIds=" + ",".join(sorted(step_ids))
    return {
        "name": "step_consistent",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson render/audio/progress evidence belongs to multiple stepIds",
    }

def _lesson_step_ack_robot_state_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    progress_step_ids = _step_ids_matching(lines, scoped, _robot_lesson_progress_success)
    ack_state_step_ids = _step_ids_matching(lines, scoped, _lesson_step_rendered_ack_with_robot_state)
    if progress_step_ids:
        missing_ack_robot_state = progress_step_ids - ack_state_step_ids
        evidence = "; ".join(
            (
                "progress=" + ",".join(sorted(progress_step_ids)),
                "ack_robot_state=" + ",".join(sorted(ack_state_step_ids)) if ack_state_step_ids else "ack_robot_state=none",
                "missing_ack_robot_state=" + ",".join(sorted(missing_ack_robot_state))
                if missing_ack_robot_state
                else "missing_ack_robot_state=none",
            )
        )
        if missing_ack_robot_state:
            return {
                "name": "lesson_step_ack_robot_state",
                "ok": False,
                "evidence": evidence,
                "missing": "completed stepIds do not all have rendered lesson_step ack robotState evidence",
            }
        return _check("lesson_step_ack_robot_state", evidence, "")

    for line in lines:
        if scoped(line) and _lesson_step_rendered_ack_with_robot_state(line):
            return _check("lesson_step_ack_robot_state", redact_line(line.strip()), "")
    return _check(
        "lesson_step_ack_robot_state",
        None,
        "no rendered lesson_step ack with robotState evidence",
    )

def _fatal_errors_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    matches: list[str] = []
    for line in lines:
        if not scoped(line):
            continue
        if any(pattern.search(line) for pattern in BENIGN_FATAL_ERROR_CONTEXT_PATTERNS):
            continue
        if any(pattern.search(line) for pattern in FATAL_ERROR_PATTERNS):
            matches.append(redact_line(line.strip()))

    if not matches:
        return _check("fatal_errors", "no fatal server/lesson errors", "")

    return {
        "name": "fatal_errors",
        "ok": False,
        "evidence": " | ".join(matches[:5]),
        "missing": "capture contains fatal server/lesson errors",
    }

def _render_not_degraded_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    matches: list[str] = []
    for line in lines:
        if not scoped(line):
            continue
        if any(pattern.search(line) for pattern in DEGRADED_RENDER_PATTERNS) or any(
            pattern.search(line) for pattern in PASSIVE_STEP_RENDER_PATTERNS
        ):
            matches.append(redact_line(line.strip()))

    if not matches:
        return _check("render_not_degraded", "no degraded render evidence", "")

    return {
        "name": "render_not_degraded",
        "ok": False,
        "evidence": " | ".join(matches[:5]),
        "missing": "lesson render was degraded or passive",
    }

def _render_not_fallback_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    matches: list[str] = []
    for line in lines:
        if not scoped(line):
            continue
        if any(pattern.search(line) for pattern in FALLBACK_RENDER_PATTERNS):
            matches.append(redact_line(line.strip()))

    if not matches:
        return _check("render_not_fallback", "no fallback/default lesson render evidence", "")

    return {
        "name": "render_not_fallback",
        "ok": False,
        "evidence": " | ".join(matches[:5]),
        "missing": "lesson render used fallback/default/idle media",
    }

def _line_dispatched_start_lesson(line: str) -> bool:
    """True when this single line shows the start_lesson tool/intent actually being
    dispatched (not failed/cancelled). Phrase-agnostic on purpose: the scope gate pairs
    this with the utterance text to detect a start triggered by a 'do not' phrase."""
    lowered = _norm(line)
    if _backend_or_server_source(lowered):
        return False
    if "start_lesson" not in lowered:
        return False
    if any(
        token in lowered
        for token in (
            "fail",
            "failed",
            "error",
            "timeout",
            "not handled",
            "ignored",
            "handled=false",
            '"handled":false',
            "handled=0",
            '"handled":0',
            "handled false",
            "dispatch=false",
            '"dispatch":false',
            "dispatched=false",
            '"dispatched":false',
            "cancelled=true",
            '"cancelled":true',
            "canceled=true",
            '"canceled":true',
            "aborted=true",
            '"aborted":true',
            "interrupted=true",
            '"interrupted":true',
            "stopped=true",
            '"stopped":true',
        )
    ):
        return False
    if "reason=start_lesson_ack" in lowered or '"reason":"start_lesson_ack"' in lowered:
        # An audible-ack line references start_lesson but is not itself a dispatch.
        return False
    return any(
        token in lowered
        for token in ("intent", "tool", "function", "command", "dispatch", "handled", "local tool")
    )

def _lesson_start_scoped_to_positive_phrases_check(
    lines: list[str], scoped: Callable[[str], bool]
) -> dict[str, Any]:
    """Assert lesson starts are scoped to positive phrases only. An offender is a
    captured line whose utterance text is a negated start phrase ("không vào khóa học")
    yet still dispatched start_lesson — i.e. the robot started a lesson for a 'do not
    start' utterance."""
    offenders: list[str] = []
    for line in lines:
        if not scoped(line):
            continue
        lowered = _norm(line)
        if not _has_negated_start_phrase(lowered):
            continue
        if _line_dispatched_start_lesson(line):
            offenders.append(redact_line(line.strip()))

    evidence = "offenders=" + (" | ".join(offenders[:3]) if offenders else "none")
    if not offenders:
        return _check("lesson_start_scoped_to_positive_phrases", evidence, "")
    return {
        "name": "lesson_start_scoped_to_positive_phrases",
        "ok": False,
        "evidence": evidence,
        "missing": "a lesson must start only for positive phrases; a negated 'do not start' phrase dispatched start_lesson",
    }

def _lesson_scoring_relevant_line(line: str) -> bool:
    lowered = _norm(line)
    return any(
        token in lowered
        for token in (
            "lesson",
            "lessonruntime",
            "stepid",
            "step_id",
            "prompt",
            "tts",
            "audio",
            "child response",
            "child_response",
            "recognizedtext",
            "recognized_text",
            "transcript",
            "serial",
            "robot",
            "teebot",
        )
    )

def _immediate_pronunciation_scoring_evidence(line: str) -> bool:
    return any(pattern.search(line) for pattern in IMMEDIATE_PRONUNCIATION_SCORING_PATTERNS) or any(
        pattern.search(line) for pattern in IMMEDIATE_CHILD_RESPONSE_EVALUATION_PATTERNS
    )

def _split_line_child_response_evaluation_step_ids(line: str) -> set[str]:
    if not _lesson_scoring_relevant_line(line):
        return set()
    if not any(pattern.search(line) for pattern in SPLIT_LINE_CHILD_RESPONSE_EVALUATION_PATTERNS):
        return set()
    return _step_ids_from_evidence(line)

def _lesson_no_immediate_pronunciation_scoring_check(
    lines: list[str], scoped: Callable[[str], bool]
) -> dict[str, Any]:
    offenders: list[str] = []
    child_response_step_ids: set[str] = set()
    for line in lines:
        if not scoped(line):
            continue
        if _lesson_scoring_relevant_line(line) and _immediate_pronunciation_scoring_evidence(line):
            offenders.append(redact_line(line.strip()))

        evaluation_step_ids = _split_line_child_response_evaluation_step_ids(line)
        if child_response_step_ids.intersection(evaluation_step_ids):
            offenders.append(redact_line(line.strip()))

        if _interactive_child_response_evidence(line) and not _lesson_progress_success(line):
            child_response_step_ids.update(_step_ids_from_evidence(line))
        if _lesson_progress_success(line):
            child_response_step_ids.difference_update(_step_ids_from_evidence(line))

    evidence = "offenders=" + (" | ".join(offenders[:3]) if offenders else "none")
    if not offenders:
        return _check("lesson_no_immediate_pronunciation_scoring", evidence, "")
    return {
        "name": "lesson_no_immediate_pronunciation_scoring",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson must guide speaking and wait for child response, not score/evaluate pronunciation immediately",
    }

def _expected_step_count(lines: list[str], scoped: Callable[[str], bool]) -> int | None:
    for line in lines:
        if not scoped(line):
            continue
        if "manifest" not in _norm(line):
            continue
        for pattern in EXPECTED_STEP_COUNT_PATTERNS:
            match = pattern.search(line)
            if match:
                return int(match.group(1))
        steps_count = _manifest_steps_array_count(line)
        if steps_count is not None:
            return steps_count
    return None

def _explicit_step_count_from_line(line: str) -> int | None:
    for pattern in EXPECTED_STEP_COUNT_PATTERNS:
        match = pattern.search(line)
        if match:
            return int(match.group(1))
    return None

def _lesson_manifest_step_count_consistency_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    compared: list[str] = []
    mismatches: list[str] = []
    explicit_counts: set[int] = set()
    steps_array_counts: set[int] = set()
    for line in lines:
        if not scoped(line) or "manifest" not in _norm(line):
            continue
        explicit_count = _explicit_step_count_from_line(line)
        steps_count = _manifest_steps_array_count(line)
        if explicit_count is not None:
            explicit_counts.add(explicit_count)
        if steps_count is not None:
            steps_array_counts.add(steps_count)
        if explicit_count is None or steps_count is None:
            continue
        label = f"totalSteps={explicit_count}; steps_array={steps_count}"
        compared.append(label)
        if explicit_count != steps_count:
            mismatches.append(label)

    all_counts = explicit_counts | steps_array_counts
    if len(all_counts) > 1:
        mismatch_parts = []
        if explicit_counts:
            mismatch_parts.append("totalSteps=" + ",".join(str(count) for count in sorted(explicit_counts)))
        if steps_array_counts:
            mismatch_parts.append("steps_array=" + ",".join(str(count) for count in sorted(steps_array_counts)))
        mismatches.append("; ".join(mismatch_parts))

    evidence_parts = []
    if compared:
        evidence_parts.append(" | ".join(compared))
    if explicit_counts:
        evidence_parts.append("totalSteps=" + ",".join(str(count) for count in sorted(explicit_counts)))
    if steps_array_counts:
        evidence_parts.append("steps_array=" + ",".join(str(count) for count in sorted(steps_array_counts)))
    evidence = "; ".join(evidence_parts) if evidence_parts else "no comparable manifest step counts"
    if not mismatches:
        return _check("lesson_manifest_step_count_consistent", evidence, "")

    return {
        "name": "lesson_manifest_step_count_consistent",
        "ok": False,
        "evidence": " | ".join(mismatches),
        "missing": "manifest totalSteps/stepCount must match steps[] count",
    }

def _manifest_steps_array_count(line: str) -> int | None:
    decoder = json.JSONDecoder()
    for match in JSON_OBJECT_START_PATTERN.finditer(line):
        try:
            value, _ = decoder.raw_decode(line[match.start():])
        except json.JSONDecodeError:
            continue
        count = _steps_array_count(value)
        if count is not None:
            return count
    return None

def _manifest_steps_array_ids(line: str) -> set[str] | None:
    decoder = json.JSONDecoder()
    for match in JSON_OBJECT_START_PATTERN.finditer(line):
        try:
            value, _ = decoder.raw_decode(line[match.start():])
        except json.JSONDecodeError:
            continue
        step_ids = _steps_array_ids(value)
        if step_ids is not None:
            return step_ids
    return None

def _manifest_steps_array_order(line: str) -> list[str] | None:
    decoder = json.JSONDecoder()
    for match in JSON_OBJECT_START_PATTERN.finditer(line):
        try:
            value, _ = decoder.raw_decode(line[match.start():])
        except json.JSONDecodeError:
            continue
        step_ids = _steps_array_order(value)
        if step_ids is not None:
            return step_ids
    return None

def _manifest_steps_array_completion_classes(line: str) -> dict[str, str] | None:
    decoder = json.JSONDecoder()
    for match in JSON_OBJECT_START_PATTERN.finditer(line):
        try:
            value, _ = decoder.raw_decode(line[match.start():])
        except json.JSONDecodeError:
            continue
        completion_classes = _steps_array_completion_classes(value)
        if completion_classes is not None:
            return completion_classes
    return None

def _manifest_steps_array_wait_for_child(line: str) -> dict[str, bool] | None:
    decoder = json.JSONDecoder()
    for match in JSON_OBJECT_START_PATTERN.finditer(line):
        try:
            value, _ = decoder.raw_decode(line[match.start():])
        except json.JSONDecodeError:
            continue
        wait_for_child = _steps_array_wait_for_child(value)
        if wait_for_child is not None:
            return wait_for_child
    return None

def _manifest_steps_array_guided_questions(line: str) -> dict[str, bool] | None:
    decoder = json.JSONDecoder()
    for match in JSON_OBJECT_START_PATTERN.finditer(line):
        try:
            value, _ = decoder.raw_decode(line[match.start():])
        except json.JSONDecodeError:
            continue
        guided_questions = _steps_array_guided_questions(value)
        if guided_questions is not None:
            return guided_questions
    return None

def _json_values_from_line(line: str) -> list[Any]:
    decoder = json.JSONDecoder()
    values: list[Any] = []
    for match in JSON_OBJECT_START_PATTERN.finditer(line):
        try:
            value, _ = decoder.raw_decode(line[match.start():])
        except json.JSONDecodeError:
            continue
        values.append(value)
    return values

def _steps_array_count(value: Any) -> int | None:
    if isinstance(value, dict):
        steps = value.get("steps")
        if isinstance(steps, list):
            return len(steps)
        for child in value.values():
            count = _steps_array_count(child)
            if count is not None:
                return count
    elif isinstance(value, list):
        for child in value:
            count = _steps_array_count(child)
            if count is not None:
                return count
    return None

def _steps_array_ids(value: Any) -> set[str] | None:
    if isinstance(value, dict):
        steps = value.get("steps")
        if isinstance(steps, list):
            ids: set[str] = set()
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_id = step.get("stepId") or step.get("id")
                if isinstance(step_id, str) and step_id:
                    ids.add(step_id)
            if ids or not steps:
                return ids
            return None
        for child in value.values():
            step_ids = _steps_array_ids(child)
            if step_ids is not None:
                return step_ids
    elif isinstance(value, list):
        for child in value:
            step_ids = _steps_array_ids(child)
            if step_ids is not None:
                return step_ids
    return None

def _steps_array_order(value: Any) -> list[str] | None:
    if isinstance(value, dict):
        steps = value.get("steps")
        if isinstance(steps, list):
            ids: list[str] = []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_id = step.get("stepId") or step.get("id")
                if isinstance(step_id, str) and step_id:
                    ids.append(step_id)
            if ids or not steps:
                return ids
            return None
        for child in value.values():
            step_ids = _steps_array_order(child)
            if step_ids is not None:
                return step_ids
    elif isinstance(value, list):
        for child in value:
            step_ids = _steps_array_order(child)
            if step_ids is not None:
                return step_ids
    return None

def _steps_array_completion_classes(value: Any) -> dict[str, str] | None:
    if isinstance(value, dict):
        steps = value.get("steps")
        if isinstance(steps, list):
            completion_classes: dict[str, str] = {}
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_id = step.get("stepId") or step.get("id")
                if not isinstance(step_id, str) or not step_id:
                    continue
                completion_class = step.get("completionClass") or step.get("completion_class")
                if isinstance(completion_class, str) and completion_class:
                    completion_classes[step_id] = _norm(completion_class)
            return completion_classes
        for child in value.values():
            completion_classes = _steps_array_completion_classes(child)
            if completion_classes is not None:
                return completion_classes
    elif isinstance(value, list):
        for child in value:
            completion_classes = _steps_array_completion_classes(child)
            if completion_classes is not None:
                return completion_classes
    return None

def _steps_array_wait_for_child(value: Any) -> dict[str, bool] | None:
    if isinstance(value, dict):
        steps = value.get("steps")
        if isinstance(steps, list):
            wait_for_child: dict[str, bool] = {}
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_id = step.get("stepId") or step.get("id")
                if not isinstance(step_id, str) or not step_id:
                    continue
                story_beat = step.get("storyBeat") or step.get("story_beat")
                if isinstance(story_beat, dict):
                    wait_for_child[step_id] = story_beat.get("waitForChild") is True or story_beat.get("wait_for_child") is True
            return wait_for_child
        for child in value.values():
            wait_for_child = _steps_array_wait_for_child(child)
            if wait_for_child is not None:
                return wait_for_child
    elif isinstance(value, list):
        for child in value:
            wait_for_child = _steps_array_wait_for_child(child)
            if wait_for_child is not None:
                return wait_for_child
    return None

def _steps_array_guided_questions(value: Any) -> dict[str, bool] | None:
    if isinstance(value, dict):
        steps = value.get("steps")
        if isinstance(steps, list):
            guided_questions: dict[str, bool] = {}
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_id = step.get("stepId") or step.get("id")
                if not isinstance(step_id, str) or not step_id:
                    continue
                story_beat = step.get("storyBeat") or step.get("story_beat")
                candidates: list[Any] = [
                    step.get("prompt"),
                    step.get("question"),
                    step.get("text"),
                    step.get("helperText"),
                    step.get("helper_text"),
                ]
                if isinstance(story_beat, dict):
                    candidates.extend(
                        story_beat.get(field)
                        for field in (
                            "ask",
                            "question",
                            "prompt",
                            "text",
                            "guidedPrompt",
                            "guided_prompt",
                        )
                    )
                guided_questions[step_id] = any(_guided_question_text(value) for value in candidates)
            return guided_questions
        for child in value.values():
            guided_questions = _steps_array_guided_questions(child)
            if guided_questions is not None:
                return guided_questions
    elif isinstance(value, list):
        for child in value:
            guided_questions = _steps_array_guided_questions(child)
            if guided_questions is not None:
                return guided_questions
    return None

def _guided_question_text(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if any(pattern.search(value) for pattern in RAW_COMMAND_ONLY_PROMPT_PATTERNS):
        return False
    return any(pattern.search(value) for pattern in GUIDED_SPEAKING_PROMPT_PATTERNS)

def _story_line_guided_question(line: str) -> bool:
    return any(_story_value_guided_question(value) for value in _json_values_from_line(line))

def _story_value_guided_question(value: Any) -> bool:
    if isinstance(value, dict):
        candidates = [
            value.get("ask"),
            value.get("question"),
            value.get("prompt"),
            value.get("text"),
            value.get("guidedPrompt"),
            value.get("guided_prompt"),
        ]
        if any(_guided_question_text(candidate) for candidate in candidates):
            return True
        return any(_story_value_guided_question(child) for child in value.values())
    if isinstance(value, list):
        return any(_story_value_guided_question(child) for child in value)
    return False

def _critical_assets_array(value: Any) -> set[str] | None:
    if isinstance(value, dict):
        for key in ("criticalAssets", "critical_assets"):
            assets = value.get(key)
            if isinstance(assets, list):
                out: set[str] = set()
                for asset in assets:
                    if isinstance(asset, str) and asset:
                        out.add(asset)
                    elif isinstance(asset, dict):
                        asset_key = asset.get("key") or asset.get("id") or asset.get("assetId")
                        if isinstance(asset_key, str) and asset_key:
                            out.add(asset_key)
                return out
        for child in value.values():
            assets = _critical_assets_array(child)
            if assets is not None:
                return assets
    elif isinstance(value, list):
        for child in value:
            assets = _critical_assets_array(child)
            if assets is not None:
                return assets
    return None

def _asset_pack_values(value: Any) -> list[dict[str, Any]]:
    packs: list[dict[str, Any]] = []
    if isinstance(value, dict):
        pack = value.get("assetPack") or value.get("asset_pack")
        if isinstance(pack, dict):
            packs.append(pack)
        for child in value.values():
            packs.extend(_asset_pack_values(child))
    elif isinstance(value, list):
        for child in value:
            packs.extend(_asset_pack_values(child))
    return packs

def _asset_pack_inline_payload_labels(value: Any, *, asset_key: str, path: str = "asset") -> list[str]:
    labels: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            lowered_key = str(key).lower()
            if lowered_key in INLINE_MEDIA_PAYLOAD_KEYS:
                labels.append(f"{asset_key}:{key}")
                continue
            labels.extend(_asset_pack_inline_payload_labels(child, asset_key=asset_key, path=child_path))
        return labels
    if isinstance(value, list):
        for index, child in enumerate(value):
            labels.extend(_asset_pack_inline_payload_labels(child, asset_key=asset_key, path=f"{path}[{index}]"))
        return labels
    if isinstance(value, str) and value.strip().lower().startswith("data:"):
        labels.append(f"{asset_key}:{path}")
    return labels

def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and value == 1:
        return True
    return isinstance(value, str) and _norm(value) in {"true", "1", "ready", "yes"}

def _local_asset_path(value: str) -> bool:
    normalized = _norm(value)
    return normalized.startswith(("sd://", "file://", "/"))

def _asset_state_ready(asset: dict[str, Any]) -> bool:
    state = asset.get("state")
    return isinstance(state, str) and _norm(state) == "ready"

def _asset_checksum_ok(asset: dict[str, Any]) -> bool:
    return asset.get("checksumOk") is True or asset.get("checksum_ok") is True

def _hex_digest(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = _norm(value)
    if not normalized or not _HEX_DIGEST_PATTERN.fullmatch(normalized):
        return None
    return normalized

def _asset_expected_digest(asset: dict[str, Any]) -> str | None:
    for key in ("sha256", "expectedSha256", "expected_sha256", "checksum", "expectedChecksum"):
        digest = _hex_digest(asset.get(key))
        if digest is not None:
            return digest
    return None

def _asset_recomputed_digest(asset: dict[str, Any]) -> str | None:
    for key in (
        "computedSha256",
        "computed_sha256",
        "localSha256",
        "local_sha256",
        "observedSha256",
        "observed_sha256",
        "actualSha256",
        "actual_sha256",
        "digest",
    ):
        digest = _hex_digest(asset.get(key))
        if digest is not None:
            return digest
    return None

def _asset_declares_positive_size(asset: dict[str, Any]) -> bool:
    size = asset.get("size")
    return isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0

def _asset_pack_cache_key_value(pack: dict[str, Any]) -> tuple[str | None, bool]:
    raw = pack["cacheKey"] if "cacheKey" in pack else pack.get("cache_key")
    if raw is None:
        return None, False
    if not isinstance(raw, str) or not raw.strip() or raw != raw.strip():
        return None, True
    return _norm(raw), False

def _identity_value(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return _norm(raw)
    return None

def _lesson_frame_identity(value: dict[str, Any]) -> tuple[str | None, str | None]:
    return (
        _identity_value(value, "assignmentId", "assignment_id"),
        _identity_value(value, "sessionId", "session_id"),
    )

def _line_frame_identity(line: str) -> tuple[str | None, str | None]:
    assignment_match = ASSIGNMENT_ID_PATTERN.search(line)
    session_ids = sorted(_session_ids_from_evidence(line))
    return (
        _norm(assignment_match.group(1)) if assignment_match else None,
        _norm(session_ids[0]) if session_ids else None,
    )

def _line_asset_pack_ready(line: str) -> bool:
    return any(pattern.search(line) for pattern in ASSET_PACK_READY_PATTERNS)

def _ready_asset_pack_local_paths(
    lines: list[str], scoped: Callable[[str], bool]
) -> tuple[
    bool,
    set[str],
    dict[tuple[str | None, str | None], set[str]],
    dict[tuple[str | None, str | None], dict[str, str]],
]:
    paths: set[str] = set()
    paths_by_identity: dict[tuple[str | None, str | None], set[str]] = {}
    paths_by_identity_key: dict[tuple[str | None, str | None], dict[str, str]] = {}
    ready = False
    for line in lines:
        if not scoped(line) or not _positive_frame("lesson_prepare")(line):
            continue
        for value in _json_values_from_line(line):
            if not isinstance(value, dict) or value.get("type") != "lesson_prepare":
                continue
            identity = _lesson_frame_identity(value)
            for pack in _asset_pack_values(value):
                if not _truthy(pack.get("ready")):
                    continue
                ready = True
                assets = pack.get("assets")
                if not isinstance(assets, list):
                    continue
                for asset in assets:
                    if not isinstance(asset, dict):
                        continue
                    local_path = asset.get("localPath") or asset.get("local_path")
                    if isinstance(local_path, str) and local_path.strip():
                        normalized_path = _norm(local_path)
                        paths.add(normalized_path)
                        paths_by_identity.setdefault(identity, set()).add(normalized_path)
                        asset_key = asset.get("key")
                        if isinstance(asset_key, str) and asset_key.strip():
                            paths_by_identity_key.setdefault(identity, {})[_norm(asset_key)] = normalized_path
    return ready, paths, paths_by_identity, paths_by_identity_key

def _lesson_step_required_source_values(value: Any) -> list[tuple[str, str | None, str | None]]:
    if not isinstance(value, dict) or value.get("type") != "lesson_step":
        return []
    body = value.get("body")
    scene = body.get("scene") if isinstance(body, dict) else None
    if not isinstance(scene, dict):
        return [
            ("backgroundScene.poster.src", None, None),
            ("teachingObject.asset.src", None, None),
            ("robotOverlay.asset.src", None, None),
        ]
    background = scene.get("backgroundScene")
    teaching = scene.get("teachingObject")
    overlay = scene.get("robotOverlay")

    def source(parent: Any, child_key: str) -> tuple[str | None, str | None]:
        child = parent.get(child_key) if isinstance(parent, dict) else None
        if not isinstance(child, dict):
            return None, None
        src = child.get("src")
        key = child.get("key") or child.get("assetKey") or child.get("asset_key")
        return (
            src if isinstance(src, str) and src.strip() else None,
            _norm(key) if isinstance(key, str) and key.strip() else None,
        )

    background_src, background_key = source(background, "poster")
    teaching_src, teaching_key = source(teaching, "asset")
    overlay_src, overlay_key = source(overlay, "asset")

    return [
        ("backgroundScene.poster.src", background_src, background_key),
        ("teachingObject.asset.src", teaching_src, teaching_key),
        ("robotOverlay.asset.src", overlay_src, overlay_key),
    ]

def _clean_lesson_text_value(value: str) -> str:
    return value.strip().strip("\"'").rstrip(",.;)")

def _lesson_step_text_key(line: str, path: str) -> str | None:
    for pattern in LESSON_STEP_TEXT_KEY_PATTERNS.get(path, ()):
        match = pattern.search(line)
        if not match:
            continue
        value = _clean_lesson_text_value(match.group(1))
        if value:
            return _norm(value)
    return None

def _lesson_step_text_source_values(
    line: str,
) -> list[tuple[str, tuple[str | None, str | None], str, str | None, str | None]]:
    step_ids = sorted(_step_ids_from_evidence(line)) or ["unknown"]
    identity = _line_frame_identity(line)
    values: list[tuple[str, tuple[str | None, str | None], str, str | None, str | None]] = []
    for path, pattern in LESSON_STEP_TEXT_SOURCE_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        src = _clean_lesson_text_value(match.group(1))
        if not src:
            continue
        expected_key = _lesson_step_text_key(line, path)
        for step_id in step_ids:
            values.append((step_id, identity, path, src, expected_key))
    return values

def _lesson_step_source_values(
    line: str,
) -> list[tuple[str, tuple[str | None, str | None], str, str | None, str | None]]:
    values: list[tuple[str, tuple[str | None, str | None], str, str | None, str | None]] = []
    for value in _json_values_from_line(line):
        if not isinstance(value, dict) or value.get("type") != "lesson_step":
            continue
        step_id = value.get("stepId") if isinstance(value.get("stepId"), str) else "unknown"
        identity = _lesson_frame_identity(value)
        for path, src, expected_key in _lesson_step_required_source_values(value):
            values.append((step_id, identity, path, src, expected_key))
    values.extend(_lesson_step_text_source_values(line))
    return values

def _lesson_step_local_source_labels(lines: list[str], scoped: Callable[[str], bool]) -> list[str]:
    labels: set[str] = set()
    for line in lines:
        if not scoped(line) or not _lesson_step_outbound(line):
            continue
        for step_id, _identity, path, src, _expected_key in _lesson_step_source_values(line):
            if isinstance(src, str) and _local_asset_path(src):
                labels.add(f"{step_id}:{path}")
    return sorted(labels)

def _lesson_step_sd_pack_sources_attested_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    ready, local_paths, local_paths_by_identity, local_paths_by_identity_key = _ready_asset_pack_local_paths(
        lines, scoped
    )
    if not ready:
        unattested_local_sources = _lesson_step_local_source_labels(lines, scoped)
        evidence = "; ".join(
            (
                "assetPack=not_ready",
                "unattested_local_sources="
                + (",".join(unattested_local_sources) if unattested_local_sources else "none"),
            )
        )
        if not unattested_local_sources:
            return _check("lesson_step_sd_pack_sources_attested", f"{evidence}; source_check=skipped", "")
        return {
            "name": "lesson_step_sd_pack_sources_attested",
            "ok": False,
            "evidence": evidence,
            "missing": "local SD/file lesson_step sources require same-session assetPack.ready=true attestation",
        }

    steps: set[str] = set()
    missing_sources: list[str] = []
    invalid_sources: list[str] = []
    missing_identity_pack_sources: list[str] = []
    stale_session_sources: list[str] = []
    missing_asset_key_sources: list[str] = []
    wrong_asset_key_sources: list[str] = []
    has_identity_packs = any(identity != (None, None) for identity in local_paths_by_identity)
    for line in lines:
        if not scoped(line) or not _lesson_step_outbound(line):
            continue
        for step_id, identity, path, src, expected_key in _lesson_step_source_values(line):
            identity_paths = local_paths_by_identity.get(identity)
            identity_paths_by_key = local_paths_by_identity_key.get(identity, {})
            steps.add(step_id)
            label = f"{step_id}:{path}"
            if src is None:
                missing_sources.append(label)
                continue
            normalized_src = _norm(src)
            if normalized_src not in local_paths:
                invalid_sources.append(label)
            elif identity_paths is None and identity != (None, None) and has_identity_packs:
                missing_identity_pack_sources.append(label)
            elif identity_paths is not None and normalized_src not in identity_paths:
                stale_session_sources.append(label)
            elif expected_key is None:
                missing_asset_key_sources.append(label)
            elif expected_key is not None and identity_paths_by_key.get(expected_key) != normalized_src:
                wrong_asset_key_sources.append(label)

    evidence = "; ".join(
        (
            "assetPack=ready",
            f"localPaths={len(local_paths)}",
            f"identityPacks={len(local_paths_by_identity)}",
            "steps=" + (",".join(sorted(steps)) if steps else "none"),
            "missing_sources=" + (",".join(missing_sources) if missing_sources else "none"),
            "invalid_sources=" + (",".join(invalid_sources) if invalid_sources else "none"),
            "missing_identity_pack_sources="
            + (",".join(missing_identity_pack_sources) if missing_identity_pack_sources else "none"),
            "stale_session_sources=" + (",".join(stale_session_sources) if stale_session_sources else "none"),
            "missing_asset_key_sources="
            + (",".join(missing_asset_key_sources) if missing_asset_key_sources else "none"),
            "wrong_asset_key_sources=" + (",".join(wrong_asset_key_sources) if wrong_asset_key_sources else "none"),
        )
    )
    if (
        local_paths
        and not missing_sources
        and not invalid_sources
        and not missing_identity_pack_sources
        and not stale_session_sources
        and not missing_asset_key_sources
        and not wrong_asset_key_sources
    ):
        return _check("lesson_step_sd_pack_sources_attested", evidence, "")
    return {
        "name": "lesson_step_sd_pack_sources_attested",
        "ok": False,
        "evidence": evidence,
        "missing": "assetPack.ready=true requires lesson_step layer src values to match ready same-session assetPack key/localPath values",
    }

def _lesson_asset_pack_keys_present_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    expected_assets = _declared_critical_assets(lines, scoped) or set()
    packs: list[dict[str, Any]] = []
    for line in lines:
        if not scoped(line) or not _positive_frame("lesson_prepare")(line):
            continue
        for value in _json_values_from_line(line):
            if not isinstance(value, dict) or value.get("type") != "lesson_prepare":
                continue
            packs.extend(_asset_pack_values(value))

    if not packs:
        return _check("lesson_asset_pack_keys_present", "assetPack=not_observed; key_check=skipped", "")

    ready_packs = [pack for pack in packs if _truthy(pack.get("ready"))]
    if not ready_packs:
        return {
            "name": "lesson_asset_pack_keys_present",
            "ok": False,
            "evidence": "assetPack=present; ready=false",
            "missing": "assetPack evidence exists but does not report ready=true",
        }

    asset_count = 0
    keyed_assets: set[str] = set()
    duplicate_keys: set[str] = set()
    missing_key_count = 0
    missing_local_path_count = 0
    remote_local_path_count = 0
    missing_size_count = 0
    non_ready_asset_count = 0
    checksum_failed_count = 0
    for pack in ready_packs:
        assets = pack.get("assets")
        if not isinstance(assets, list):
            missing_key_count += 1
            missing_local_path_count += 1
            missing_size_count += 1
            non_ready_asset_count += 1
            checksum_failed_count += 1
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                missing_key_count += 1
                missing_local_path_count += 1
                missing_size_count += 1
                non_ready_asset_count += 1
                checksum_failed_count += 1
                continue
            asset_count += 1
            asset_key = asset.get("key")
            if not isinstance(asset_key, str) or not asset_key.strip():
                missing_key_count += 1
            local_path = asset.get("localPath") or asset.get("local_path")
            if not isinstance(local_path, str) or not local_path.strip():
                missing_local_path_count += 1
            elif not _local_asset_path(local_path):
                remote_local_path_count += 1
            if not _asset_declares_positive_size(asset):
                missing_size_count += 1
            if not _asset_state_ready(asset):
                non_ready_asset_count += 1
            if not _asset_checksum_ok(asset):
                checksum_failed_count += 1
            if not isinstance(asset_key, str) or not asset_key.strip():
                continue
            normalized_key = _norm(asset_key)
            if normalized_key in keyed_assets:
                duplicate_keys.add(normalized_key)
            keyed_assets.add(normalized_key)

    required_assets = {_norm(asset) for asset in expected_assets if asset}
    missing_required = sorted(required_assets - keyed_assets)
    evidence = "; ".join(
        (
            "assetPack=ready",
            f"assets={asset_count}",
            f"keyed={len(keyed_assets)}",
            f"missing_key={missing_key_count}",
            f"missing_localPath={missing_local_path_count}",
            f"remote_localPath={remote_local_path_count}",
            f"missing_size={missing_size_count}",
            f"non_ready_asset={non_ready_asset_count}",
            f"checksum_failed={checksum_failed_count}",
            "duplicate_key=" + (",".join(sorted(duplicate_keys)) if duplicate_keys else "none"),
            "missing_required=" + (",".join(missing_required) if missing_required else "none"),
        )
    )
    if (
        missing_key_count == 0
        and missing_local_path_count == 0
        and remote_local_path_count == 0
        and missing_size_count == 0
        and non_ready_asset_count == 0
        and checksum_failed_count == 0
        and not duplicate_keys
        and not missing_required
    ):
        return _check("lesson_asset_pack_keys_present", evidence, "")
    return {
        "name": "lesson_asset_pack_keys_present",
        "ok": False,
        "evidence": evidence,
        "missing": "assetPack.ready=true requires non-empty keys, local paths, positive size, READY state, and checksumOk=true for all declared critical assets",
    }

def _lesson_asset_pack_no_inline_media_payloads_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    inline_payloads: list[str] = []
    for line in lines:
        if not scoped(line) or not _positive_frame("lesson_prepare")(line):
            continue
        for value in _json_values_from_line(line):
            if not isinstance(value, dict) or value.get("type") != "lesson_prepare":
                continue
            for pack in _asset_pack_values(value):
                assets = pack.get("assets")
                if not isinstance(assets, list):
                    continue
                for index, asset in enumerate(assets):
                    if not isinstance(asset, dict):
                        continue
                    raw_key = asset.get("key")
                    asset_key = _norm(raw_key) if isinstance(raw_key, str) and raw_key.strip() else f"asset[{index}]"
                    inline_payloads.extend(_asset_pack_inline_payload_labels(asset, asset_key=asset_key, path="asset"))

    evidence = "inline_payload=" + (",".join(sorted(set(inline_payloads))) if inline_payloads else "none")
    if not inline_payloads:
        return _check("lesson_asset_pack_no_inline_media_payloads", evidence, "")
    return {
        "name": "lesson_asset_pack_no_inline_media_payloads",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson_prepare assetPack must reference SD/file assets by metadata, not inline image payloads",
    }

def _asset_layer_group(asset_key: str) -> str | None:
    normalized = _norm(asset_key)
    if normalized.startswith("backgroundscene."):
        return "backgroundScene"
    if normalized.startswith("teachingobject."):
        return "teachingObject"
    if normalized.startswith("robotoverlay."):
        return "robotOverlay"
    return None

def _lesson_asset_pack_required_layer_groups_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    required_groups = {"backgroundScene", "teachingObject", "robotOverlay"}
    observed_groups: set[str] = set()
    ready_pack_count = 0
    for line in lines:
        if not scoped(line) or not _positive_frame("lesson_prepare")(line):
            continue
        for value in _json_values_from_line(line):
            if not isinstance(value, dict) or value.get("type") != "lesson_prepare":
                continue
            for pack in _asset_pack_values(value):
                if not _truthy(pack.get("ready")):
                    continue
                ready_pack_count += 1
                assets = pack.get("assets")
                if not isinstance(assets, list):
                    continue
                for asset in assets:
                    if not isinstance(asset, dict):
                        continue
                    asset_key = asset.get("key")
                    if not isinstance(asset_key, str) or not asset_key.strip():
                        continue
                    group = _asset_layer_group(asset_key)
                    if group:
                        observed_groups.add(group)

    missing_groups = sorted(required_groups - observed_groups)
    evidence = "; ".join(
        (
            f"ready_packs={ready_pack_count}",
            "groups=" + (",".join(sorted(observed_groups)) if observed_groups else "none"),
            "missing_groups=" + (",".join(missing_groups) if missing_groups else "none"),
        )
    )
    if ready_pack_count == 0 or not missing_groups:
        return _check("lesson_asset_pack_required_layer_groups", evidence, "")
    return {
        "name": "lesson_asset_pack_required_layer_groups",
        "ok": False,
        "evidence": evidence,
        "missing": "ready lesson assetPack must include backgroundScene, teachingObject, and robotOverlay asset groups",
    }

def _ready_asset_pack_cache_keys(
    lines: list[str], scoped: Callable[[str], bool]
) -> dict[tuple[str | None, str | None], set[str]]:
    cache_keys: dict[tuple[str | None, str | None], set[str]] = {}
    for line in lines:
        if not scoped(line) or not _positive_frame("lesson_prepare")(line):
            continue
        for value in _json_values_from_line(line):
            if not isinstance(value, dict) or value.get("type") != "lesson_prepare":
                continue
            identity = _lesson_frame_identity(value)
            for pack in _asset_pack_values(value):
                if not _truthy(pack.get("ready")):
                    continue
                cache_key, invalid_cache_key = _asset_pack_cache_key_value(pack)
                if cache_key and not invalid_cache_key:
                    cache_keys.setdefault(identity, set()).add(cache_key)
                else:
                    cache_keys.setdefault(identity, set()).add("<missing-cache-key>")
    return cache_keys

def _single_expected_ready_pack_identity(
    expected: dict[tuple[str | None, str | None], set[str]], cache_key: str
) -> tuple[str | None, str | None] | None:
    expected_pairs = [
        (identity, expected_cache_key)
        for identity, cache_keys in expected.items()
        for expected_cache_key in cache_keys
    ]
    if len(expected_pairs) != 1:
        return None
    identity, expected_cache_key = expected_pairs[0]
    if expected_cache_key != cache_key:
        return None
    return identity

def _lesson_asset_pack_ack_ready_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    expected = _ready_asset_pack_cache_keys(lines, scoped)
    if not expected:
        return _check("lesson_asset_pack_ack_ready", "assetPack=not_ready; ack_check=skipped", "")

    lesson_start_at: dict[tuple[str | None, str | None], int] = {}
    for line_number, line in enumerate(lines):
        if not scoped(line) or _lesson_frame_outbound_type(line) != "lesson_start":
            continue
        identity = _line_frame_identity(line)
        for value in _json_values_from_line(line):
            if isinstance(value, dict) and value.get("type") == "lesson_start":
                identity = _lesson_frame_identity(value)
                break
        lesson_start_at.setdefault(identity, line_number)

    ack_ready: dict[tuple[str | None, str | None], set[str]] = {}
    invalid_ack_cache_keys: list[str] = []
    late_ack_cache_keys: list[str] = []
    for line_number, line in enumerate(lines):
        if not scoped(line) or not _lesson_ack_positive(1)(line):
            continue
        if _line_asset_pack_ready(line):
            for cache_key in _ids_from_evidence(line, CACHE_KEY_PATTERNS):
                normalized_cache_key = _norm(cache_key)
                identity = _line_frame_identity(line)
                if identity == (None, None):
                    identity = _single_expected_ready_pack_identity(expected, normalized_cache_key) or identity
                start_line = lesson_start_at.get(identity)
                if start_line is not None and line_number > start_line:
                    assignment_id, session_id = identity
                    late_ack_cache_keys.append(
                        f"{assignment_id or '?'}:{session_id or '?'}:{normalized_cache_key}"
                    )
                    continue
                ack_ready.setdefault(identity, set()).add(normalized_cache_key)
        for value in _json_values_from_line(line):
            if not isinstance(value, dict) or value.get("type") != "lesson_ack":
                continue
            body = value.get("body")
            if not isinstance(body, dict):
                continue
            pack = body.get("assetPack") or body.get("asset_pack")
            if not isinstance(pack, dict) or not _truthy(pack.get("ready")):
                continue
            cache_key, invalid_cache_key = _asset_pack_cache_key_value(pack)
            if invalid_cache_key:
                assignment_id, session_id = _lesson_frame_identity(value)
                invalid_ack_cache_keys.append(f"{assignment_id or '?'}:{session_id or '?'}")
                continue
            if not cache_key:
                continue
            identity = _lesson_frame_identity(value)
            start_line = lesson_start_at.get(identity)
            if start_line is not None and line_number > start_line:
                assignment_id, session_id = identity
                late_ack_cache_keys.append(f"{assignment_id or '?'}:{session_id or '?'}:{cache_key}")
                continue
            ack_ready.setdefault(identity, set()).add(cache_key)

    missing: list[str] = []
    for identity, cache_keys in sorted(expected.items(), key=lambda item: str(item[0])):
        ack_keys = ack_ready.get(identity, set())
        for cache_key in sorted(cache_keys):
            if cache_key not in ack_keys:
                assignment_id, session_id = identity
                missing.append(f"{assignment_id or '?'}:{session_id or '?'}:{cache_key}")

    expected_label = ",".join(
        f"{identity[0] or '?'}:{identity[1] or '?'}:{cache_key}"
        for identity, cache_keys in sorted(expected.items(), key=lambda item: str(item[0]))
        for cache_key in sorted(cache_keys)
    )
    ack_label = ",".join(
        f"{identity[0] or '?'}:{identity[1] or '?'}:{cache_key}"
        for identity, cache_keys in sorted(ack_ready.items(), key=lambda item: str(item[0]))
        for cache_key in sorted(cache_keys)
    )
    evidence = "; ".join(
        (
            "prepare_ready=" + (expected_label if expected_label else "none"),
            "ack_ready=" + (ack_label if ack_label else "none"),
            "invalid_ack_cache_key="
            + (",".join(sorted(set(invalid_ack_cache_keys))) if invalid_ack_cache_keys else "none"),
            "late_ack=" + (",".join(sorted(set(late_ack_cache_keys))) if late_ack_cache_keys else "none"),
            "missing_ack=" + (",".join(missing) if missing else "none"),
        )
    )
    if not missing and not invalid_ack_cache_keys and not late_ack_cache_keys:
        return _check("lesson_asset_pack_ack_ready", evidence, "")
    return {
        "name": "lesson_asset_pack_ack_ready",
        "ok": False,
        "evidence": evidence,
        "missing": "assetPack.ready=true prepare evidence requires firmware lesson_prepare ack with matching canonical assetPack.ready=true cacheKey",
    }

def _lesson_asset_pack_cache_key_matches_manifest_checksum_check(
    lines: list[str], scoped: Callable[[str], bool]
) -> dict[str, Any]:
    expected: list[tuple[tuple[str | None, str | None], str, str]] = []
    invalid_cache_keys: list[str] = []
    prepare_predicate = _positive_frame("lesson_prepare")
    for line in lines:
        if not scoped(line) or not prepare_predicate(line):
            continue
        for value in _json_values_from_line(line):
            if not isinstance(value, dict) or value.get("type") != "lesson_prepare":
                continue
            body = value.get("body")
            if not isinstance(body, dict):
                continue
            manifest_ref = body.get("manifestRef") or body.get("manifest_ref")
            if not isinstance(manifest_ref, dict):
                continue
            checksum = manifest_ref.get("manifestChecksum") or manifest_ref.get("manifest_checksum")
            if not isinstance(checksum, str) or not checksum.strip():
                continue
            for pack in _asset_pack_values(body):
                if not _truthy(pack.get("ready")):
                    continue
                identity = _lesson_frame_identity(value)
                cache_key, invalid_cache_key = _asset_pack_cache_key_value(pack)
                if invalid_cache_key:
                    assignment_id, session_id = identity
                    invalid_cache_keys.append(f"{assignment_id or '?'}:{session_id or '?'}")
                    continue
                if cache_key:
                    expected.append((identity, cache_key, _norm(checksum)))

    if not expected and not invalid_cache_keys:
        return _check(
            "lesson_asset_pack_cache_key_matches_manifest_checksum",
            "assetPack=not_ready_or_checksum_missing; cache_key_checksum_check=skipped",
            "",
        )

    stale: list[str] = []
    observed: list[str] = []
    for identity, cache_key, checksum in expected:
        assignment_id, session_id = identity
        label = f"{assignment_id or '?'}:{session_id or '?'}:{cache_key}->{checksum}"
        observed.append(label)
        if checksum not in cache_key:
            stale.append(f"{assignment_id or '?'}:{session_id or '?'}:{cache_key}")

    evidence = "; ".join(
        (
            "cache_key_checks=" + ",".join(observed),
            "invalid_cache_key=" + (",".join(sorted(set(invalid_cache_keys))) if invalid_cache_keys else "none"),
            "stale_cache_key=" + (",".join(stale) if stale else "none"),
        )
    )
    if not stale and not invalid_cache_keys:
        return _check("lesson_asset_pack_cache_key_matches_manifest_checksum", evidence, "")
    return {
        "name": "lesson_asset_pack_cache_key_matches_manifest_checksum",
        "ok": False,
        "evidence": evidence,
        "missing": "ready SD assetPack cacheKey must be a canonical non-empty string containing the full current manifest checksum so edited course content cannot reuse stale SD bytes",
    }

def _lesson_asset_pack_sha256_attested_check(
    lines: list[str], scoped: Callable[[str], bool]
) -> dict[str, Any]:
    """T14-LIVE-02 cold-preload sha256 gate: when an asset reports both the manifest-declared
    expected digest and a recomputed on-SD digest, the two must be equal. A
    firmware that asserts checksumOk=true while the recomputed digest differs from
    (or is missing for) the expected digest is rejected -- the self-reported boolean
    is not trusted on its own when a real digest pair is available to corroborate.
    """
    prepare_predicate = _positive_frame("lesson_prepare")
    total = 0
    attested = 0
    mismatch: list[str] = []
    unattested: list[str] = []
    for line in lines:
        if not scoped(line) or not prepare_predicate(line):
            continue
        for value in _json_values_from_line(line):
            if not isinstance(value, dict) or value.get("type") != "lesson_prepare":
                continue
            assignment_id, session_id = _lesson_frame_identity(value)
            for pack in _asset_pack_values(value):
                if not _truthy(pack.get("ready")):
                    continue
                assets = pack.get("assets")
                if not isinstance(assets, list):
                    continue
                for index, asset in enumerate(assets):
                    if not isinstance(asset, dict):
                        continue
                    expected = _asset_expected_digest(asset)
                    recomputed = _asset_recomputed_digest(asset)
                    if expected is None and recomputed is None:
                        continue
                    total += 1
                    raw_key = asset.get("key")
                    asset_key = (
                        _norm(raw_key) if isinstance(raw_key, str) and raw_key.strip() else f"asset[{index}]"
                    )
                    label = f"{assignment_id or '?'}:{session_id or '?'}:{asset_key}"
                    if expected is None or recomputed is None:
                        unattested.append(label)
                    elif expected != recomputed:
                        mismatch.append(f"{label}({expected[:8]}!={recomputed[:8]})")
                    else:
                        attested += 1

    if total == 0:
        return _check(
            "lesson_asset_pack_sha256_attested",
            "assetPack=no_digest_evidence; sha256_attestation=skipped",
            "",
        )

    evidence = "; ".join(
        (
            f"digest_assets={total}",
            f"attested={attested}",
            "mismatch=" + (",".join(mismatch) if mismatch else "none"),
            "unattested=" + (",".join(unattested) if unattested else "none"),
        )
    )
    if not mismatch and not unattested:
        return _check("lesson_asset_pack_sha256_attested", evidence, "")
    return {
        "name": "lesson_asset_pack_sha256_attested",
        "ok": False,
        "evidence": evidence,
        "missing": "ready SD assetPack assets that report a recomputed sha256 must match the manifest-declared expected sha256 (and provide both digests) so checksumOk=true cannot mask wrong bytes",
    }

def _lesson_asset_pack_cache_key_version_segment_check(
    lines: list[str], scoped: Callable[[str], bool]
) -> dict[str, Any]:
    """T14-LIVE-02 cache directory shape "<lesson>/v<version>-<checksum>": the cacheKey
    must literally encode a trailing "v<version>-<checksum>" segment whose checksum
    tail equals the current manifest checksum and whose version equals the prepared
    lessonVersion (when present). "cacheKey merely contains the checksum" is not
    enough -- the canonical version-pinned directory shape must actually be present.
    """
    prepare_predicate = _positive_frame("lesson_prepare")
    invalid_cache_keys: list[str] = []
    no_segment: list[str] = []
    checksum_mismatch: list[str] = []
    version_mismatch: list[str] = []
    observed: list[str] = []
    for line in lines:
        if not scoped(line) or not prepare_predicate(line):
            continue
        for value in _json_values_from_line(line):
            if not isinstance(value, dict) or value.get("type") != "lesson_prepare":
                continue
            body = value.get("body")
            if not isinstance(body, dict):
                continue
            manifest_ref = body.get("manifestRef") or body.get("manifest_ref")
            if not isinstance(manifest_ref, dict):
                continue
            checksum = manifest_ref.get("manifestChecksum") or manifest_ref.get("manifest_checksum")
            if not isinstance(checksum, str) or not checksum.strip():
                continue
            checksum = _norm(checksum)
            lesson_version = body.get("lessonVersion") or body.get("lesson_version")
            version_str: str | None = None
            if isinstance(lesson_version, bool):
                version_str = None
            elif isinstance(lesson_version, int):
                version_str = str(lesson_version)
            elif isinstance(lesson_version, str) and lesson_version.strip().isdigit():
                version_str = lesson_version.strip()
            for pack in _asset_pack_values(body):
                if not _truthy(pack.get("ready")):
                    continue
                identity = _lesson_frame_identity(value)
                assignment_id, session_id = identity
                cache_key, invalid_cache_key = _asset_pack_cache_key_value(pack)
                prefix = f"{assignment_id or '?'}:{session_id or '?'}"
                if invalid_cache_key:
                    invalid_cache_keys.append(prefix)
                    continue
                if not cache_key:
                    continue
                observed.append(f"{prefix}:{cache_key}")
                match = CACHE_KEY_VERSION_SEGMENT_PATTERN.search(cache_key)
                if not match:
                    no_segment.append(f"{prefix}:{cache_key}")
                    continue
                segment_version, segment_checksum = match.group(1), _norm(match.group(2))
                # The version-segment checksum tail must encode the current manifest
                # checksum. Mirror the existing (looser) "cacheKey contains checksum"
                # gate by accepting when either value is a prefix/substring of the
                # other (manifests may be abbreviated in evidence), but reject a tail
                # that shares no overlap with the manifest checksum (the genuine
                # stale/wrong-checksum case).
                if checksum not in segment_checksum and segment_checksum not in checksum:
                    checksum_mismatch.append(f"{prefix}:{cache_key}")
                if version_str is not None and segment_version != version_str:
                    version_mismatch.append(f"{prefix}:{cache_key}(v{segment_version}!=v{version_str})")

    if not observed and not invalid_cache_keys:
        return _check(
            "lesson_asset_pack_cache_key_version_segment",
            "assetPack=not_ready_or_checksum_missing; cache_key_version_segment_check=skipped",
            "",
        )

    evidence = "; ".join(
        (
            "cache_keys=" + (",".join(observed) if observed else "none"),
            "invalid_cache_key=" + (",".join(sorted(set(invalid_cache_keys))) if invalid_cache_keys else "none"),
            "no_version_segment=" + (",".join(no_segment) if no_segment else "none"),
            "checksum_mismatch=" + (",".join(checksum_mismatch) if checksum_mismatch else "none"),
            "version_mismatch=" + (",".join(version_mismatch) if version_mismatch else "none"),
        )
    )
    if not invalid_cache_keys and not no_segment and not checksum_mismatch and not version_mismatch:
        return _check("lesson_asset_pack_cache_key_version_segment", evidence, "")
    return {
        "name": "lesson_asset_pack_cache_key_version_segment",
        "ok": False,
        "evidence": evidence,
        "missing": "ready SD assetPack cacheKey must literally encode the canonical <lesson>/v<version>-<checksum> directory shape with the current manifest checksum and matching lessonVersion",
    }

def _declared_critical_assets(lines: list[str], scoped: Callable[[str], bool]) -> set[str] | None:
    for line in lines:
        if not scoped(line) or not _positive_frame("lesson_prepare")(line):
            continue
        for value in _json_values_from_line(line):
            assets = _critical_assets_array(value)
            if assets is not None:
                return {_norm(asset) for asset in assets}
    return None

WARM_CACHE_MARKER_PATTERNS = (
    re.compile(r"(?i)\bcache[_-]?hit[\"']?\s*[:=]\s*(?:[\"']?true\b|1\b)"),
    re.compile(r"(?i)\bre[_-]?attested[\"']?\s*[:=]\s*(?:[\"']?true\b|[1-9]\d*\b)"),
    re.compile(r"(?i)\breused\b[^\n]*\bcached?\b"),
    re.compile(r"(?i)\bwarm[_-]?(?:cache|start)\b"),
)
# downloaded=0 is only a warm-cache signal alongside an explicit cache/re-attest
# marker; on cold-start preload lines downloaded=0 means "not ready" instead.
WARM_CACHE_DOWNLOADED_ZERO_PATTERN = re.compile(r"(?i)\bdownloaded[\"']?\s*[:=]\s*[\"']?0\b(?![.\d])")
ASSET_DOWNLOADED_COUNT_PATTERN = re.compile(r"(?i)\bdownloaded[\"']?\s*[:=]\s*[\"']?([1-9]\d*)\b")
# A positive download = the ESP server pulling origin bytes onto SD this session.
# Rendering an already-cached asset (fetched+drawn) is NOT a download.
ASSET_DOWNLOAD_EVIDENCE_PATTERNS = (
    re.compile(r"(?i)\basset\b[^\n]*\bdownload(?:ed|ing)?\b"),
    re.compile(r"(?i)\bdownload(?:ed|ing)?\b[^\n]*\basset\b"),
    re.compile(r"(?i)\bfetch[_+\s-]?(?:and[_\s-]?)?store\b"),
    re.compile(r"(?i)\bfetched[_+\s-]?(?:and[_\s-]?)?stored\b"),
)


def _warm_cache_marker(line: str) -> bool:
    lowered = _norm(line)
    if any(pattern.search(line) for pattern in WARM_CACHE_MARKER_PATTERNS):
        return True
    # An assetPack JSON marker reporting a cache hit / re-attest counts too.
    for value in _json_values_from_line(line):
        for pack in _asset_pack_values(value):
            if _truthy(pack.get("cacheHit")) or _truthy(pack.get("cache_hit")):
                return True
            reattested = pack.get("reattested", pack.get("re_attested"))
            if reattested is True or (
                isinstance(reattested, (int, float)) and not isinstance(reattested, bool) and reattested >= 1
            ):
                return True
    # downloaded=0 alone is ambiguous; require the line to also be asset-pack
    # scoped so cold-start preload_ready lines are not mistaken for warm cache.
    if WARM_CACHE_DOWNLOADED_ZERO_PATTERN.search(line) and "assetpack" in lowered:
        return True
    return False


def _asset_download_evidence(line: str) -> bool:
    lowered = _norm(line)
    # A render that draws an asset from a local/remote source is not a download.
    if "fetched+drawn" in lowered or "fetched + drawn" in lowered:
        return False
    if any(pattern.search(line) for pattern in ASSET_DOWNLOAD_EVIDENCE_PATTERNS):
        # Ignore the negated/zero form ("downloaded=0", "no asset download").
        if "no asset download" in lowered or "downloaded=0" in lowered.replace(" ", ""):
            return False
        return True
    return False


def _asset_download_label(line: str) -> str:
    keys = _ids_from_evidence(line, (re.compile(r"(?i)\bkey[\"']?\s*[:=]\s*[\"']?([^,\"'}\s;]+)"),))
    step_ids = sorted(_step_ids_from_evidence(line))
    if keys:
        return ",".join(sorted(_norm(key) for key in keys))
    if step_ids:
        return ",".join(step_ids)
    return "unknown"


def _lesson_warm_cache_no_redownload_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    marker_present = False
    contradictory_counts: list[str] = []
    for line in lines:
        if not scoped(line) or not _warm_cache_marker(line):
            continue
        marker_present = True
        # A warm-cache marker that also reports a positive downloaded count is
        # self-contradictory evidence and must be rejected.
        match = ASSET_DOWNLOADED_COUNT_PATTERN.search(line)
        if match:
            contradictory_counts.append(match.group(1))
        for value in _json_values_from_line(line):
            for pack in _asset_pack_values(value):
                downloaded = pack.get("downloaded")
                if (
                    isinstance(downloaded, (int, float))
                    and not isinstance(downloaded, bool)
                    and downloaded > 0
                ):
                    contradictory_counts.append(str(int(downloaded)))

    if not marker_present:
        return _check(
            "lesson_warm_cache_no_redownload",
            "warmCacheMarker=absent; redownload_check=skipped",
            "",
        )

    download_labels: list[str] = []
    for line in lines:
        if not scoped(line) or not _asset_download_evidence(line):
            continue
        download_labels.append(_asset_download_label(line))

    evidence = "; ".join(
        (
            "warmCacheMarker=present",
            "downloaded_count="
            + (",".join(sorted(set(contradictory_counts))) if contradictory_counts else "0"),
            "asset_downloads=" + (",".join(sorted(set(download_labels))) if download_labels else "none"),
        )
    )
    if not contradictory_counts and not download_labels:
        return _check("lesson_warm_cache_no_redownload", evidence, "")
    return {
        "name": "lesson_warm_cache_no_redownload",
        "ok": False,
        "evidence": evidence,
        "missing": "warm-start cache hit must re-attest cached bytes without downloading assets this session",
    }


def _lesson_preload_critical_assets_ready_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    expected_assets = _declared_critical_assets(lines, scoped)
    if expected_assets is None:
        return _check("lesson_preload_critical_assets_ready", "expected=unknown; ready=unknown", "")
    if not expected_assets:
        return _check("lesson_preload_critical_assets_ready", "expected=none; ready=none", "")

    ready_assets: set[str] = set()
    all_ready = False
    for line in lines:
        if not scoped(line) or not _lesson_preload_ready(line):
            continue
        lowered = _norm(line)
        if any(
            token in lowered
            for token in (
                "criticalassets=ready",
                '"criticalassets":"ready"',
                "critical assets ready",
                "all critical assets ready",
            )
        ):
            all_ready = True
        ready_assets.update(asset for asset in expected_assets if asset in lowered)

    evidence = "; ".join(
        (
            "expected=" + ",".join(sorted(expected_assets)),
            "ready=all" if all_ready else "ready=" + (",".join(sorted(ready_assets)) if ready_assets else "none"),
        )
    )
    if all_ready or expected_assets.issubset(ready_assets):
        return _check("lesson_preload_critical_assets_ready", evidence, "")

    return {
        "name": "lesson_preload_critical_assets_ready",
        "ok": False,
        "evidence": evidence,
        "missing": "preload_ready does not confirm declared critical assets are ready",
    }

def _expected_manifest_step_ids(lines: list[str], scoped: Callable[[str], bool]) -> set[str] | None:
    for line in lines:
        if not scoped(line):
            continue
        if "manifest" not in _norm(line):
            continue
        step_ids = _manifest_steps_array_ids(line)
        if step_ids is not None:
            return step_ids
    return None

def _expected_manifest_step_order(lines: list[str], scoped: Callable[[str], bool]) -> list[str] | None:
    for line in lines:
        if not scoped(line):
            continue
        if "manifest" not in _norm(line):
            continue
        step_ids = _manifest_steps_array_order(line)
        if step_ids is not None:
            return step_ids
    return None

def _expected_manifest_step_completion_classes(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, str] | None:
    for line in lines:
        if not scoped(line):
            continue
        if "manifest" not in _norm(line):
            continue
        completion_classes = _manifest_steps_array_completion_classes(line)
        if completion_classes is not None:
            return completion_classes
    return None

def _lesson_manifest_completion_classes_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    expected_step_ids = _expected_manifest_step_ids(lines, scoped)
    completion_classes = _expected_manifest_step_completion_classes(lines, scoped)
    if expected_step_ids is None and completion_classes is None:
        return {
            "name": "lesson_manifest_completion_classes",
            "ok": False,
            "evidence": "manifest_completionClass=not_declared",
            "missing": "manifest steps[] must declare completionClass passive or interactive for every step",
        }

    expected_step_ids = expected_step_ids or set(completion_classes or {})
    completion_classes = completion_classes or {}
    missing = expected_step_ids - set(completion_classes)
    invalid = {
        step_id: completion_class
        for step_id, completion_class in completion_classes.items()
        if completion_class not in {"passive", "interactive"}
    }
    evidence = "; ".join(
        (
            "manifest=" + ",".join(sorted(expected_step_ids)) if expected_step_ids else "manifest=none",
            "classes=" + ",".join(
                f"{step_id}:{completion_classes[step_id]}" for step_id in sorted(completion_classes)
            ) if completion_classes else "classes=none",
            "missing_completionClass=" + ",".join(sorted(missing)) if missing else "missing_completionClass=none",
            "invalid_completionClass=" + ",".join(
                f"{step_id}:{completion_class}" for step_id, completion_class in sorted(invalid.items())
            ) if invalid else "invalid_completionClass=none",
        )
    )
    if not missing and not invalid:
        return _check("lesson_manifest_completion_classes", evidence, "")

    return {
        "name": "lesson_manifest_completion_classes",
        "ok": False,
        "evidence": evidence,
        "missing": "manifest steps[] must declare completionClass passive or interactive for every step",
    }

def _lesson_manifest_steps_consistency_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    orders: list[tuple[str, ...]] = []
    for line in lines:
        if not scoped(line) or "manifest" not in _norm(line):
            continue
        step_order = _manifest_steps_array_order(line)
        if step_order is None:
            continue
        orders.append(tuple(step_order))

    unique_orders = sorted(set(orders))
    evidence = (
        "manifest_steps=" + " | ".join(",".join(order) if order else "empty" for order in unique_orders)
        if unique_orders
        else "manifest_steps=not_declared"
    )
    if len(unique_orders) <= 1:
        return _check("lesson_manifest_steps_consistent", evidence, "")

    return {
        "name": "lesson_manifest_steps_consistent",
        "ok": False,
        "evidence": evidence,
        "missing": "manifest steps[] ids/order must be consistent across fetches",
    }

def _ordered_step_ids_matching(
    lines: list[str],
    scoped: Callable[[str], bool],
    predicate: Callable[[str], bool],
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if not scoped(line) or not predicate(line):
            continue
        for step_id in sorted(_step_ids_from_evidence(line)):
            if step_id in seen:
                continue
            seen.add(step_id)
            ordered.append(step_id)
    return ordered

def _step_id_counts_matching(
    lines: list[str],
    scoped: Callable[[str], bool],
    predicate: Callable[[str], bool],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in lines:
        if not scoped(line) or not predicate(line):
            continue
        step_ids = sorted(_step_ids_from_evidence(line)) or ["unknown"]
        for step_id in step_ids:
            counts[step_id] = counts.get(step_id, 0) + 1
    return counts

def _lesson_step_outbound(line: str) -> bool:
    if not _positive_frame("lesson_step")(line):
        return False
    lowered = _norm(line)
    return not any(token in lowered for token in ("serial rx", "rx lesson_step"))

def _lesson_frame_outbound_type(line: str) -> str | None:
    lowered = _norm(line)
    if "serial rx" in lowered:
        return None
    for frame_type in ("lesson_prepare", "lesson_start", "lesson_step", "lesson_stop"):
        if not _positive_frame(frame_type)(line):
            continue
        if frame_type == "lesson_step" and not _lesson_step_outbound(line):
            continue
        return frame_type
    return None

def _lesson_sequence_from_line(line: str) -> int | None:
    for pattern in LESSON_SEQUENCE_PATTERNS:
        match = pattern.search(line)
        if match:
            return int(match.group(1))
    return None

def _lesson_sequence_monotonic_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    frames: list[tuple[str, int]] = []
    for line in lines:
        if not scoped(line):
            continue
        frame_type = _lesson_frame_outbound_type(line)
        if frame_type is None:
            continue
        sequence = _lesson_sequence_from_line(line)
        if sequence is None:
            continue
        frames.append((frame_type, sequence))

    if not frames:
        return _check("lesson_sequence_monotonic", "frames=none; sequence=not_declared", "")

    ordered = ",".join(f"{frame_type}:{sequence}" for frame_type, sequence in frames)
    previous = -1
    non_monotonic: list[str] = []
    for frame_type, sequence in frames:
        if sequence <= previous:
            non_monotonic.append(f"{frame_type}:{sequence}")
        previous = sequence

    evidence = "; ".join(
        (
            f"frames={ordered}",
            "non_monotonic=" + (",".join(non_monotonic) if non_monotonic else "none"),
        )
    )
    if not non_monotonic:
        return _check("lesson_sequence_monotonic", evidence, "")

    return {
        "name": "lesson_sequence_monotonic",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson control frame sequence must strictly increase",
    }

def _lesson_wire_frame_size_budget_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    observed: list[str] = []
    oversized: list[str] = []
    for line in lines:
        if not scoped(line):
            continue
        for value in _json_values_from_line(line):
            if not isinstance(value, dict):
                continue
            frame_type = value.get("type")
            if frame_type not in {"lesson_prepare", "lesson_start", "lesson_step", "lesson_stop"}:
                continue
            if frame_type == "lesson_step" and not _lesson_step_outbound(line):
                continue
            if frame_type != _lesson_frame_outbound_type(line):
                continue
            payload_bytes = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
            step_id = value.get("stepId") if isinstance(value.get("stepId"), str) else "-"
            label = f"{frame_type}:{step_id}:{payload_bytes}"
            observed.append(label)
            if payload_bytes > MAX_LESSON_FRAME_BYTES:
                oversized.append(label)

    evidence = "; ".join(
        (
            f"maxBytes={MAX_LESSON_FRAME_BYTES}",
            "frames=" + (",".join(observed) if observed else "none"),
            "oversized=" + (",".join(oversized) if oversized else "none"),
        )
    )
    if not oversized:
        return _check("lesson_wire_frame_size_budget", evidence, "")
    return {
        "name": "lesson_wire_frame_size_budget",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson wire frames must stay within the ESP runtime MAX_LESSON_FRAME_BYTES budget",
    }

def _lesson_ack_sequence_match_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    expected: set[tuple[str, int]] = set()
    actual: set[tuple[str, int]] = set()
    ack_sequences_declared = False

    for line in lines:
        if not scoped(line):
            continue
        frame_type = _lesson_frame_outbound_type(line)
        if frame_type in {"lesson_prepare", "lesson_start", "lesson_step"}:
            sequence = _lesson_sequence_from_line(line)
            if sequence is not None:
                expected.add((frame_type, sequence))

        lowered = _norm(line)
        if "lesson_ack" not in lowered:
            continue
        sequence = _lesson_sequence_from_line(line)
        ack_number = _ack_number_from_line(line)
        if sequence is None or ack_number is None:
            continue
        ack_sequences_declared = True
        actual.add((_ack_frame_type(ack_number), sequence))

    def label(pairs: set[tuple[str, int]]) -> str:
        return ",".join(f"{frame_type}:{sequence}" for frame_type, sequence in sorted(pairs)) if pairs else "none"

    if not expected:
        return _check("lesson_ack_sequence_match", "expected=none; ack_sequences=not_required", "")
    if not ack_sequences_declared:
        return _check("lesson_ack_sequence_match", f"expected={label(expected)}; ack_sequences=not_declared", "")

    missing = expected - actual
    unexpected = actual - expected
    evidence = "; ".join(
        (
            f"expected={label(expected)}",
            f"actual={label(actual)}",
            f"missing_ack={label(missing)}",
            f"unexpected_ack={label(unexpected)}",
        )
    )
    if not missing and not unexpected:
        return _check("lesson_ack_sequence_match", evidence, "")

    return {
        "name": "lesson_ack_sequence_match",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson_ack sequence must match the outbound lesson frame it acknowledges",
    }

def _lesson_stop_sequence_match_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    sent_sequences: set[int] = set()
    received_sequences: set[int] = set()
    stop_receive_seen = False

    for line in lines:
        if not scoped(line):
            continue
        if _lesson_frame_outbound_type(line) == "lesson_stop":
            sequence = _lesson_sequence_from_line(line)
            if sequence is not None:
                sent_sequences.add(sequence)
        if _lesson_stop_received(line):
            stop_receive_seen = True
            sequence = _lesson_sequence_from_line(line)
            if sequence is not None:
                received_sequences.add(sequence)

    def label(values: set[int]) -> str:
        return ",".join(str(value) for value in sorted(values)) if values else "none"

    if not sent_sequences:
        return _check("lesson_stop_sequence_match", "sent=none; received=not_required", "")
    if stop_receive_seen and not received_sequences:
        return _check("lesson_stop_sequence_match", f"sent={label(sent_sequences)}; received=not_declared", "")

    missing = sent_sequences - received_sequences
    unexpected = received_sequences - sent_sequences
    evidence = "; ".join(
        (
            f"sent={label(sent_sequences)}",
            f"received={label(received_sequences)}",
            f"missing_received={label(missing)}",
            f"unexpected_received={label(unexpected)}",
        )
    )
    if not missing and not unexpected:
        return _check("lesson_stop_sequence_match", evidence, "")

    return {
        "name": "lesson_stop_sequence_match",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson_stop receive/ack sequence must match the outbound lesson_stop frame",
    }

def _lesson_progress_sequence_after_step_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    step_sequences: dict[str, set[int]] = {}
    progress_sequences: dict[str, set[int]] = {}

    for line in lines:
        if not scoped(line):
            continue
        sequence = _lesson_sequence_from_line(line)
        if sequence is None:
            continue
        step_ids = _step_ids_from_evidence(line)
        if not step_ids:
            continue
        if _lesson_step_outbound(line):
            for step_id in step_ids:
                step_sequences.setdefault(step_id, set()).add(sequence)
        if _robot_lesson_progress_success(line):
            for step_id in step_ids:
                progress_sequences.setdefault(step_id, set()).add(sequence)

    stale_progress: list[str] = []
    for step_id in sorted(set(step_sequences) & set(progress_sequences)):
        max_step_sequence = max(step_sequences[step_id])
        for progress_sequence in sorted(progress_sequences[step_id]):
            if progress_sequence < max_step_sequence:
                stale_progress.append(f"{step_id}:{progress_sequence}<{max_step_sequence}")

    def label(values_by_step: dict[str, set[int]]) -> str:
        if not values_by_step:
            return "none"
        return ",".join(
            f"{step_id}:{'/'.join(str(value) for value in sorted(values))}"
            for step_id, values in sorted(values_by_step.items())
        )

    evidence = "; ".join(
        (
            f"step_sequences={label(step_sequences)}",
            f"progress_sequences={label(progress_sequences)}",
            "stale_progress=" + (",".join(stale_progress) if stale_progress else "none"),
        )
    )
    if not stale_progress:
        return _check("lesson_progress_sequence_after_step", evidence, "")

    return {
        "name": "lesson_progress_sequence_after_step",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson_progress sequence must not be older than the matching lesson_step sequence",
    }

def _lesson_stop_sequence_after_progress_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    stop_sequences: set[int] = set()
    progress_sequences: set[int] = set()

    for line in lines:
        if not scoped(line):
            continue
        sequence = _lesson_sequence_from_line(line)
        if sequence is None:
            continue
        if _lesson_frame_outbound_type(line) == "lesson_stop":
            stop_sequences.add(sequence)
        if _robot_lesson_progress_success(line):
            progress_sequences.add(sequence)

    def label(values: set[int]) -> str:
        return ",".join(str(value) for value in sorted(values)) if values else "none"

    stale_stop: list[str] = []
    if stop_sequences and progress_sequences:
        max_progress_sequence = max(progress_sequences)
        stale_stop = [f"{sequence}<{max_progress_sequence}" for sequence in sorted(stop_sequences) if sequence < max_progress_sequence]

    evidence = "; ".join(
        (
            f"progress={label(progress_sequences)}",
            f"stop={label(stop_sequences)}",
            "stale_stop=" + (",".join(stale_stop) if stale_stop else "none"),
        )
    )
    if not stale_stop:
        return _check("lesson_stop_sequence_after_progress", evidence, "")

    return {
        "name": "lesson_stop_sequence_after_progress",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson_stop sequence must not be older than lesson_progress sequence",
    }

def _lesson_progress_count_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    expected = _expected_step_count(lines, scoped)
    success_lines = [line for line in lines if scoped(line) and _robot_lesson_progress_success(line)]
    progress_step_ids: set[str] = set()
    for line in success_lines:
        progress_step_ids.update(_step_ids_from_evidence(line))
    # A PASSIVE step never reports step_completed: per the runtime's own wire contract
    # (core/lesson/runtime.py:268-274) "the FIRMWARE NEVER emits a step_completed
    # progress for it. It AUTO-ADVANCES once the firmware acks the lesson_step". Only
    # interactive steps, where the child actually answers, produce one. Counting
    # step_completed alone therefore demanded an event the firmware is documented never
    # to send, and could not pass for ANY real lesson, simulated or live: a clean 9-step
    # run yields 9 step_started and 4 step_completed (the four interactive steps).
    #
    # A step is complete if it was answered (step_completed) OR rendered and acked,
    # which is exactly what auto-advance means on the wire.
    # _first_indices_matching keys on whatever token it can extract, which for an ack line
    # includes correlation tokens like "acks=12" as well as the real step id. Counting those
    # inflated the total past the manifest step count (observed 12/9), so keep only real
    # step ids -- a key carrying "=" is a correlation token, not a step.
    rendered_step_ids = {
        step_id
        for step_id in _first_indices_matching(lines, scoped, _lesson_step_rendered_ack)
        if "=" not in step_id
    }
    completed_step_ids = progress_step_ids | rendered_step_ids
    successes = len(completed_step_ids) if completed_step_ids else len(success_lines)
    count_label = (
        "completed step count (step_completed or render-ack)"
        if completed_step_ids
        else "successful step_completed count"
    )
    if expected is None:
        return _check("lesson_progress_count", f"{count_label}={successes}; expected not declared", "")
    if expected < 1:
        return {
            "name": "lesson_progress_count",
            "ok": False,
            "evidence": f"{count_label}={successes}; expected_steps={expected}",
            "missing": "manifest must declare at least one lesson step",
        }
    if successes == expected:
        return _check("lesson_progress_count", f"{count_label}={successes}/{expected}", "")
    return {
        "name": "lesson_progress_count",
        "ok": False,
        "evidence": f"{count_label}={successes}/{expected}",
        "missing": "not all manifest steps completed successfully",
    }

def _lesson_progress_unique_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    progress_counts = _step_id_counts_matching(lines, scoped, _robot_lesson_progress_success)
    posted_counts = _step_id_counts_matching(lines, scoped, _backend_progress_posted)
    duplicate_progress = {step_id: count for step_id, count in progress_counts.items() if count > 1}
    duplicate_posted = {step_id: count for step_id, count in posted_counts.items() if count > 1}

    def label(counts: dict[str, int]) -> str:
        return ",".join(f"{step_id}:{count}" for step_id, count in sorted(counts.items())) if counts else "none"

    evidence = "; ".join(
        (
            f"duplicate_progress={label(duplicate_progress)}",
            f"duplicate_posted={label(duplicate_posted)}",
        )
    )
    if not duplicate_progress and not duplicate_posted:
        return _check("lesson_progress_unique", evidence, "")

    return {
        "name": "lesson_progress_unique",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson_progress/backend persist evidence repeated for the same stepId",
    }

def _lesson_step_playback_unique_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    duplicate_groups = {
        "duplicate_step": {
            step_id: count
            for step_id, count in _step_id_counts_matching(lines, scoped, _lesson_step_outbound).items()
            if count > 1
        },
        "duplicate_content": {
            step_id: count
            for step_id, count in _step_id_counts_matching(lines, scoped, _lesson_content_rendered).items()
            if count > 1
        },
        "duplicate_overlay": {
            step_id: count
            for step_id, count in _step_id_counts_matching(lines, scoped, _robot_overlay_rendered).items()
            if count > 1
        },
        "duplicate_audio": {
            step_id: count
            for step_id, count in _step_id_counts_matching(lines, scoped, _lesson_audio_played).items()
            if count > 1
        },
        "duplicate_ack": {
            step_id: count
            for step_id, count in _step_id_counts_matching(lines, scoped, _lesson_step_rendered_ack).items()
            if count > 1
        },
    }

    def label(counts: dict[str, int]) -> str:
        return ",".join(f"{step_id}:{count}" for step_id, count in sorted(counts.items())) if counts else "none"

    evidence = "; ".join(f"{name}={label(counts)}" for name, counts in duplicate_groups.items())
    if not any(duplicate_groups.values()):
        return _check("lesson_step_playback_unique", evidence, "")

    return {
        "name": "lesson_step_playback_unique",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson step playback/render/audio/ack evidence repeated for the same stepId",
    }

def _lesson_manifest_step_ids_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    expected_step_ids = _expected_manifest_step_ids(lines, scoped)
    progress_step_ids = _step_ids_matching(lines, scoped, _robot_lesson_progress_success)
    if expected_step_ids is None:
        return {
            "name": "lesson_manifest_step_ids",
            "ok": False,
            "evidence": "manifest_steps=not_declared; progress=" + (
                ",".join(sorted(progress_step_ids)) if progress_step_ids else "none"
            ),
            "missing": "manifest steps[] ids must be present before completed stepIds can be trusted",
        }

    missing_completed = expected_step_ids - progress_step_ids
    unexpected_completed = progress_step_ids - expected_step_ids
    evidence = "; ".join(
        (
            "manifest=" + ",".join(sorted(expected_step_ids)) if expected_step_ids else "manifest=none",
            "progress=" + ",".join(sorted(progress_step_ids)) if progress_step_ids else "progress=none",
            "missing_completed=" + ",".join(sorted(missing_completed)) if missing_completed else "missing_completed=none",
            "unexpected_completed=" + ",".join(sorted(unexpected_completed)) if unexpected_completed else "unexpected_completed=none",
        )
    )
    if not missing_completed and not unexpected_completed:
        return _check("lesson_manifest_step_ids", evidence, "")

    return {
        "name": "lesson_manifest_step_ids",
        "ok": False,
        "evidence": evidence,
        "missing": "completed stepIds must match manifest steps[] ids",
    }

def _lesson_manifest_step_order_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    expected_order = _expected_manifest_step_order(lines, scoped)
    if expected_order is None:
        return {
            "name": "lesson_manifest_step_order",
            "ok": False,
            "evidence": "manifest_step_order=not_declared",
            "missing": "manifest steps[] order must be present before completed step order can be trusted",
        }

    progress_order = _ordered_step_ids_matching(lines, scoped, _robot_lesson_progress_success)
    evidence = "; ".join(
        (
            "manifest=" + ",".join(expected_order) if expected_order else "manifest=none",
            "progress=" + ",".join(progress_order) if progress_order else "progress=none",
        )
    )
    if progress_order == expected_order:
        return _check("lesson_manifest_step_order", evidence, "")

    return {
        "name": "lesson_manifest_step_order",
        "ok": False,
        "evidence": evidence,
        "missing": "completed stepIds must follow manifest steps[] order",
    }

def _lesson_progress_step_identity_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    expected = _expected_step_count(lines, scoped)
    progress_lines = [line for line in lines if scoped(line) and _robot_lesson_progress_success(line)]
    posted_lines = [line for line in lines if scoped(line) and _backend_progress_posted(line)]
    progress_without_step_id = [str(index) for index, line in enumerate(progress_lines) if not _step_ids_from_evidence(line)]
    posted_without_step_id = [str(index) for index, line in enumerate(posted_lines) if not _step_ids_from_evidence(line)]
    evidence = "; ".join(
        (
            f"expected_steps={expected if expected is not None else 'not_declared'}",
            f"progress_without_stepId={len(progress_without_step_id)}",
            f"posted_without_stepId={len(posted_without_step_id)}",
        )
    )
    if expected is None or expected < 1 or not (progress_without_step_id or posted_without_step_id):
        return _check("lesson_progress_step_identity", evidence, "")

    return {
        "name": "lesson_progress_step_identity",
        "ok": False,
        "evidence": evidence,
        "missing": "multi-step lesson_progress and backend progress evidence must include stepId",
    }

def _lesson_progress_posted_steps_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    progress_step_ids = _step_ids_matching(lines, scoped, _robot_lesson_progress_success)
    posted_step_ids = _step_ids_matching(lines, scoped, _backend_progress_posted)
    missing_posted = progress_step_ids - posted_step_ids
    evidence = "; ".join(
        (
            "progress=" + ",".join(sorted(progress_step_ids)) if progress_step_ids else "progress=none",
            "posted=" + ",".join(sorted(posted_step_ids)) if posted_step_ids else "posted=none",
            "missing_posted=" + ",".join(sorted(missing_posted)) if missing_posted else "missing_posted=none",
        )
    )
    if not progress_step_ids or not missing_posted:
        return _check("lesson_progress_posted_steps", evidence, "")

    return {
        "name": "lesson_progress_posted_steps",
        "ok": False,
        "evidence": evidence,
        "missing": "not all completed stepIds have backend lesson_progress persist evidence",
    }


def _lesson_backend_progress_ordered_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    progress = _first_indices_matching(lines, scoped, _robot_lesson_progress_success)
    posted = _first_indices_matching(lines, scoped, _backend_progress_posted)
    posted_before_progress: list[str] = []
    missing_progress: list[str] = []
    for step_id, posted_index in sorted(posted.items()):
        progress_index = progress.get(step_id)
        if progress_index is None:
            missing_progress.append(step_id)
            continue
        if posted_index < progress_index:
            posted_before_progress.append(step_id)

    evidence = "; ".join(
        (
            "progress=" + ",".join(sorted(progress)) if progress else "progress=none",
            "posted=" + ",".join(sorted(posted)) if posted else "posted=none",
            "posted_before_progress=" + ",".join(posted_before_progress) if posted_before_progress else "posted_before_progress=none",
            "missing_progress=" + ",".join(missing_progress) if missing_progress else "missing_progress=none",
        )
    )
    if not posted_before_progress and not missing_progress:
        return _check("lesson_backend_progress_ordered", evidence, "")

    return {
        "name": "lesson_backend_progress_ordered",
        "ok": False,
        "evidence": evidence,
        "missing": "backend lesson_progress must be persisted after robot lesson_progress for the same stepId",
    }

def _step_sessions_matching(
    lines: list[str],
    scoped: Callable[[str], bool],
    predicate: Callable[[str], bool],
) -> dict[str, set[str]]:
    sessions_by_step: dict[str, set[str]] = {}
    for line in lines:
        if not scoped(line) or not predicate(line):
            continue
        step_ids = _step_ids_from_evidence(line)
        session_ids = _session_ids_from_evidence(line)
        for step_id in step_ids:
            sessions_by_step.setdefault(step_id, set()).update(session_ids)
    return sessions_by_step

def _lesson_backend_progress_session_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    progress_sessions = _step_sessions_matching(lines, scoped, _robot_lesson_progress_success)
    posted_sessions = _step_sessions_matching(lines, scoped, _backend_progress_posted)
    missing_posted_sessions: list[str] = []
    session_mismatch: list[str] = []
    for step_id in sorted(set(progress_sessions) & set(posted_sessions)):
        robot_step_sessions = progress_sessions[step_id]
        backend_step_sessions = posted_sessions[step_id]
        if robot_step_sessions and not backend_step_sessions:
            missing_posted_sessions.append(step_id)
            continue
        if robot_step_sessions and backend_step_sessions and robot_step_sessions.isdisjoint(backend_step_sessions):
            session_mismatch.append(step_id)

    evidence = "; ".join(
        (
            "progress_sessions=" + ",".join(f"{step}:{'/'.join(sorted(sessions))}" for step, sessions in sorted(progress_sessions.items()))
            if progress_sessions
            else "progress_sessions=none",
            "posted_sessions=" + ",".join(f"{step}:{'/'.join(sorted(sessions))}" for step, sessions in sorted(posted_sessions.items()))
            if posted_sessions
            else "posted_sessions=none",
            "missing_posted_sessions=" + ",".join(missing_posted_sessions)
            if missing_posted_sessions
            else "missing_posted_sessions=none",
            "session_mismatch=" + ",".join(session_mismatch) if session_mismatch else "session_mismatch=none",
        )
    )
    if not missing_posted_sessions and not session_mismatch:
        return _check("lesson_backend_progress_session", evidence, "")

    return {
        "name": "lesson_backend_progress_session",
        "ok": False,
        "evidence": evidence,
        "missing": "backend lesson_progress sessionId must match robot lesson_progress sessionId for the same stepId",
    }

def _lesson_backend_progress_before_stop_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    stop_index: int | None = None
    stop_predicate = _sent_frame("lesson_stop")
    for index, line in enumerate(lines):
        if scoped(line) and stop_predicate(line):
            stop_index = index
            break

    if stop_index is None:
        return _check("lesson_backend_progress_before_stop", None, "no lesson_stop frame evidence")

    posted_after_stop: list[str] = []
    for line in lines[stop_index + 1 :]:
        if not scoped(line) or not _backend_progress_posted(line):
            continue
        posted_after_stop.extend(sorted(_step_ids_from_evidence(line)) or ["unknown"])

    evidence = (
        f"stop_index={stop_index}; posted_after_stop=" + ",".join(posted_after_stop)
        if posted_after_stop
        else f"stop_index={stop_index}; posted_after_stop=none"
    )
    if not posted_after_stop:
        return _check("lesson_backend_progress_before_stop", evidence, "")

    return {
        "name": "lesson_backend_progress_before_stop",
        "ok": False,
        "evidence": evidence,
        "missing": "backend lesson_progress persisted after lesson_stop",
    }

def _lesson_completion_after_backend_progress_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    completion_index: int | None = None
    for index, line in enumerate(lines):
        if scoped(line) and _backend_completion_posted(line):
            completion_index = index
            break

    if completion_index is None:
        return _check(
            "lesson_completion_after_backend_progress",
            None,
            "no backend lesson completion post/persist evidence",
        )

    posted_after_completion: list[str] = []
    for line in lines[completion_index + 1 :]:
        if not scoped(line) or not _backend_progress_posted(line):
            continue
        posted_after_completion.extend(sorted(_step_ids_from_evidence(line)) or ["unknown"])

    evidence = (
        f"completion_index={completion_index}; posted_after_completion=" + ",".join(posted_after_completion)
        if posted_after_completion
        else f"completion_index={completion_index}; posted_after_completion=none"
    )
    if not posted_after_completion:
        return _check("lesson_completion_after_backend_progress", evidence, "")

    return {
        "name": "lesson_completion_after_backend_progress",
        "ok": False,
        "evidence": evidence,
        "missing": "backend lesson_completed posted before all backend lesson_progress persisted",
    }

def _runtime_lesson_completed(line: str) -> bool:
    lowered = _norm(line)
    if _backend_or_server_source(lowered):
        return False
    if not _lesson_completed_positive(line):
        return False
    if _backend_completion_posted(line) or _assignment_completed(line):
        return False
    return True

def _lesson_runtime_completion_after_backend_progress_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    completion_index: int | None = None
    for index, line in enumerate(lines):
        if scoped(line) and _runtime_lesson_completed(line):
            completion_index = index
            break

    if completion_index is None:
        return _check("lesson_runtime_completion_after_backend_progress", "runtime_completion=none", "")

    posted_after_completion: list[str] = []
    for line in lines[completion_index + 1 :]:
        if not scoped(line) or not _backend_progress_posted(line):
            continue
        posted_after_completion.extend(sorted(_step_ids_from_evidence(line)) or ["unknown"])

    evidence = (
        f"runtime_completion_index={completion_index}; posted_after_runtime_completion=" + ",".join(posted_after_completion)
        if posted_after_completion
        else f"runtime_completion_index={completion_index}; posted_after_runtime_completion=none"
    )
    if not posted_after_completion:
        return _check("lesson_runtime_completion_after_backend_progress", evidence, "")

    return {
        "name": "lesson_runtime_completion_after_backend_progress",
        "ok": False,
        "evidence": evidence,
        "missing": "runtime lesson_completed occurred before all backend lesson_progress persisted",
    }

def _lesson_completion_session_match_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    progress_sessions: set[str] = set()
    completion_sessions: set[str] = set()
    for line in lines:
        if not scoped(line):
            continue
        if _robot_lesson_progress_success(line) or _backend_progress_posted(line):
            progress_sessions.update(_session_ids_from_evidence(line))
        if _backend_completion_posted(line):
            completion_sessions.update(_session_ids_from_evidence(line))

    missing_completion_session = bool(progress_sessions and not completion_sessions)
    mismatch = bool(progress_sessions and completion_sessions and not completion_sessions <= progress_sessions)
    evidence = "; ".join(
        (
            "progress_sessions=" + ",".join(sorted(progress_sessions)) if progress_sessions else "progress_sessions=none",
            "completion_sessions=" + ",".join(sorted(completion_sessions)) if completion_sessions else "completion_sessions=none",
            "session_missing=yes" if missing_completion_session else "session_missing=no",
            "session_mismatch=yes" if mismatch else "session_mismatch=no",
        )
    )
    if not missing_completion_session and not mismatch:
        return _check("lesson_completion_session_match", evidence, "")

    return {
        "name": "lesson_completion_session_match",
        "ok": False,
        "evidence": evidence,
        "missing": "backend lesson_completed sessionId must be present and match lesson_progress sessionId",
    }

def _step_ids_matching(
    lines: list[str],
    scoped: Callable[[str], bool],
    predicate: Callable[[str], bool],
) -> set[str]:
    step_ids: set[str] = set()
    for line in lines:
        if not scoped(line) or not predicate(line):
            continue
        step_ids.update(_step_ids_from_evidence(line))
    return step_ids

def _updates_current_step_context(line: str) -> bool:
    return (
        _positive_frame("lesson_step")(line)
        or _lesson_step_started(line)
        or _lesson_step_rendered_ack(line)
        or _lesson_audio_played(line)
    )

def _contextual_step_ids(line: str, current_step_id: str | None) -> set[str]:
    step_ids = _step_ids_from_evidence(line)
    if step_ids:
        return step_ids
    return {current_step_id} if current_step_id else set()

def _step_ids_matching_with_current_step_context(
    lines: list[str],
    scoped: Callable[[str], bool],
    predicate: Callable[[str], bool],
) -> set[str]:
    step_ids: set[str] = set()
    current_step_id: str | None = None
    for line in lines:
        if not scoped(line):
            continue
        line_step_ids = _step_ids_from_evidence(line)
        if line_step_ids and _updates_current_step_context(line):
            current_step_id = sorted(line_step_ids)[0]
        if not predicate(line):
            continue
        step_ids.update(_contextual_step_ids(line, current_step_id))
    return step_ids

def _first_indices_matching_with_current_step_context(
    lines: list[str],
    scoped: Callable[[str], bool],
    predicate: Callable[[str], bool],
) -> dict[str, int]:
    indices: dict[str, int] = {}
    current_step_id: str | None = None
    for index, line in enumerate(lines):
        if not scoped(line):
            continue
        line_step_ids = _step_ids_from_evidence(line)
        if line_step_ids and _updates_current_step_context(line):
            current_step_id = sorted(line_step_ids)[0]
        if not predicate(line):
            continue
        for step_id in _contextual_step_ids(line, current_step_id):
            indices.setdefault(step_id, index)
    return indices

def _interactive_step_ids(lines: list[str], scoped: Callable[[str], bool]) -> set[str]:
    manifest_completion_classes = _expected_manifest_step_completion_classes(lines, scoped) or {}
    interactive_step_ids = {
        step_id for step_id, completion_class in manifest_completion_classes.items() if completion_class == "interactive"
    }
    passive_step_ids = {
        step_id for step_id, completion_class in manifest_completion_classes.items() if completion_class == "passive"
    }
    for line in lines:
        if not scoped(line) or not _positive_frame("lesson_step")(line):
            continue
        if "interactive" not in _completion_classes_from_evidence(line):
            continue
        interactive_step_ids.update(_step_ids_from_evidence(line))
    return interactive_step_ids

def _lesson_steps_observed_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    progress_step_ids = _step_ids_matching(lines, scoped, _robot_lesson_progress_success)
    sent_step_ids = _step_ids_matching(lines, scoped, _positive_frame("lesson_step"))
    started_step_ids = _step_ids_matching(lines, scoped, _lesson_step_started)
    rendered_step_ids = _step_ids_matching(lines, scoped, _background_rendered_with_media)
    audio_step_ids = _step_ids_matching(lines, scoped, _lesson_audio_played)
    ack_step_ids = _step_ids_matching(lines, scoped, _lesson_step_rendered_ack)

    missing_sent = progress_step_ids - sent_step_ids
    missing_started = progress_step_ids - started_step_ids
    missing_render = progress_step_ids - rendered_step_ids
    missing_audio = progress_step_ids - audio_step_ids
    missing_ack = progress_step_ids - ack_step_ids
    evidence = "; ".join(
        (
            "progress=" + ",".join(sorted(progress_step_ids)) if progress_step_ids else "progress=none",
            "sent=" + ",".join(sorted(sent_step_ids)) if sent_step_ids else "sent=none",
            "started=" + ",".join(sorted(started_step_ids)) if started_step_ids else "started=none",
            "rendered=" + ",".join(sorted(rendered_step_ids)) if rendered_step_ids else "rendered=none",
            "audio=" + ",".join(sorted(audio_step_ids)) if audio_step_ids else "audio=none",
            "ack=" + ",".join(sorted(ack_step_ids)) if ack_step_ids else "ack=none",
            "missing_sent=" + ",".join(sorted(missing_sent)) if missing_sent else "missing_sent=none",
            "missing_started=" + ",".join(sorted(missing_started)) if missing_started else "missing_started=none",
            "missing_render=" + ",".join(sorted(missing_render)) if missing_render else "missing_render=none",
            "missing_audio=" + ",".join(sorted(missing_audio)) if missing_audio else "missing_audio=none",
            "missing_ack=" + ",".join(sorted(missing_ack)) if missing_ack else "missing_ack=none",
        )
    )
    if not progress_step_ids or not (missing_sent or missing_started or missing_render or missing_audio or missing_ack):
        return _check("lesson_steps_observed", evidence, "")

    return {
        "name": "lesson_steps_observed",
        "ok": False,
        "evidence": evidence,
        "missing": "completed stepIds do not all have lesson_step, step_started, render, audio, and rendered ack evidence",
    }

def _interactive_child_response_observed_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    manifest_completion_classes = _expected_manifest_step_completion_classes(lines, scoped) or {}
    interactive_step_ids = {
        step_id for step_id, completion_class in manifest_completion_classes.items() if completion_class == "interactive"
    }
    passive_step_ids = {
        step_id for step_id, completion_class in manifest_completion_classes.items() if completion_class == "passive"
    }
    for line in lines:
        if not scoped(line) or not _positive_frame("lesson_step")(line):
            continue
        if "interactive" not in _completion_classes_from_evidence(line):
            continue
        interactive_step_ids.update(_step_ids_from_evidence(line))

    progress_step_ids = _step_ids_matching(lines, scoped, _robot_lesson_progress_success)
    response_step_ids = _step_ids_matching(lines, scoped, _interactive_child_response_evidence)
    completed_interactive = interactive_step_ids & progress_step_ids
    missing_response = completed_interactive - response_step_ids
    passive_response = passive_step_ids & response_step_ids
    missing_interactive_lesson = bool(progress_step_ids) and not interactive_step_ids
    evidence = "; ".join(
        (
            "interactive=" + ",".join(sorted(interactive_step_ids)) if interactive_step_ids else "interactive=none",
            "passive=" + ",".join(sorted(passive_step_ids)) if passive_step_ids else "passive=none",
            "progress=" + ",".join(sorted(progress_step_ids)) if progress_step_ids else "progress=none",
            "child_response=" + ",".join(sorted(response_step_ids)) if response_step_ids else "child_response=none",
            "missing_child_response=" + ",".join(sorted(missing_response)) if missing_response else "missing_child_response=none",
            "passive_response=" + ",".join(sorted(passive_response)) if passive_response else "passive_response=none",
            "missing_interactive_lesson=true" if missing_interactive_lesson else "missing_interactive_lesson=false",
        )
    )
    if not missing_interactive_lesson and not missing_response and not passive_response:
        return _check("interactive_child_response_observed", evidence, "")

    return {
        "name": "interactive_child_response_observed",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson flows must include interactive stepIds with child response evidence; passive stepIds must not have child response evidence",
    }

def _interactive_child_response_window_opened_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    manifest_completion_classes = _expected_manifest_step_completion_classes(lines, scoped) or {}
    passive_step_ids = {
        step_id for step_id, completion_class in manifest_completion_classes.items() if completion_class == "passive"
    }
    interactive_step_ids = _interactive_step_ids(lines, scoped)
    progress_step_ids = _step_ids_matching(lines, scoped, _robot_lesson_progress_success)
    window_indices = _first_indices_matching_with_current_step_context(lines, scoped, _interactive_child_response_window_opened)
    response_indices = _first_indices_matching(lines, scoped, _interactive_child_response_evidence)
    guided_prompt_indices = _first_indices_matching_with_current_step_context(lines, scoped, _guided_speaking_prompt_handoff)
    audio_indices = _first_indices_matching(lines, scoped, _lesson_audio_played)
    ack_indices = _first_indices_matching(lines, scoped, _lesson_step_rendered_ack)
    window_step_ids = set(window_indices)
    completed_interactive = interactive_step_ids & progress_step_ids
    missing_window = sorted(completed_interactive - window_step_ids)
    window_before_robot_turn = sorted(
        step_id
        for step_id in completed_interactive & window_step_ids
        if step_id in audio_indices
        and step_id in ack_indices
        and (window_indices[step_id] <= audio_indices[step_id] or window_indices[step_id] <= ack_indices[step_id])
    )
    window_after_response = sorted(
        step_id
        for step_id in completed_interactive & window_step_ids & set(response_indices)
        if window_indices[step_id] >= response_indices[step_id]
    )
    window_before_guided_prompt = sorted(
        step_id
        for step_id in completed_interactive & window_step_ids & set(guided_prompt_indices)
        if window_indices[step_id] <= guided_prompt_indices[step_id]
    )
    passive_window = sorted(passive_step_ids & window_step_ids)
    evidence = "; ".join(
        (
            "interactive=" + (",".join(sorted(interactive_step_ids)) if interactive_step_ids else "none"),
            "passive=" + (",".join(sorted(passive_step_ids)) if passive_step_ids else "none"),
            "progress=" + (",".join(sorted(progress_step_ids)) if progress_step_ids else "none"),
            "window=" + (",".join(sorted(window_step_ids)) if window_step_ids else "none"),
            "missing_window=" + (",".join(missing_window) if missing_window else "none"),
            "window_before_robot_turn=" + (",".join(window_before_robot_turn) if window_before_robot_turn else "none"),
            "window_after_response=" + (",".join(window_after_response) if window_after_response else "none"),
            "window_before_guided_prompt=" + (",".join(window_before_guided_prompt) if window_before_guided_prompt else "none"),
            "passive_window=" + (",".join(passive_window) if passive_window else "none"),
        )
    )
    if not missing_window and not window_before_robot_turn and not window_after_response and not window_before_guided_prompt and not passive_window:
        return _check("interactive_child_response_window_opened", evidence, "")

    return {
        "name": "interactive_child_response_window_opened",
        "ok": False,
        "evidence": evidence,
        "missing": "interactive stepIds must ask a guided prompt before opening response windows; passive stepIds must not open response windows",
    }

def _interactive_child_response_ordered_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    manifest_completion_classes = _expected_manifest_step_completion_classes(lines, scoped) or {}
    interactive_step_ids = {
        step_id for step_id, completion_class in manifest_completion_classes.items() if completion_class == "interactive"
    }
    for line in lines:
        if not scoped(line) or not _positive_frame("lesson_step")(line):
            continue
        if "interactive" not in _completion_classes_from_evidence(line):
            continue
        interactive_step_ids.update(_step_ids_from_evidence(line))

    progress = _first_indices_matching(lines, scoped, _robot_lesson_progress_success)
    responses = _first_indices_matching(lines, scoped, _interactive_child_response_evidence)
    audio = _first_indices_matching(lines, scoped, _lesson_audio_played)
    ack = _first_indices_matching(lines, scoped, _lesson_step_rendered_ack)

    completed_interactive = interactive_step_ids & set(progress)
    response_before_robot_turn: list[str] = []
    response_after_progress: list[str] = []
    missing_order_data: list[str] = []
    for step_id in sorted(completed_interactive & set(responses)):
        response_index = responses[step_id]
        audio_index = audio.get(step_id)
        ack_index = ack.get(step_id)
        progress_index = progress.get(step_id)
        if audio_index is None or ack_index is None or progress_index is None:
            missing_order_data.append(step_id)
            continue
        if response_index <= audio_index or response_index <= ack_index:
            response_before_robot_turn.append(step_id)
        if response_index >= progress_index:
            response_after_progress.append(step_id)

    evidence = "; ".join(
        (
            "interactive=" + (",".join(sorted(interactive_step_ids)) if interactive_step_ids else "none"),
            "response=" + (",".join(sorted(responses)) if responses else "none"),
            "response_before_robot_turn=" + (",".join(response_before_robot_turn) if response_before_robot_turn else "none"),
            "response_after_progress=" + (",".join(response_after_progress) if response_after_progress else "none"),
            "missing_order_data=" + (",".join(missing_order_data) if missing_order_data else "none"),
        )
    )
    if not response_before_robot_turn and not response_after_progress and not missing_order_data:
        return _check("interactive_child_response_ordered", evidence, "")

    return {
        "name": "interactive_child_response_ordered",
        "ok": False,
        "evidence": evidence,
        "missing": "interactive child response must occur after robot audio/render ack and before step progress",
    }

def _lesson_step_prompt_after_frame_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    sent = _first_indices_matching(lines, scoped, _lesson_step_outbound)
    prompts = _first_indices_matching_with_current_step_context(lines, scoped, _lesson_step_prompt_handoff)
    progress_step_ids = _step_ids_matching(lines, scoped, _robot_lesson_progress_success)

    prompt_before_frame: list[str] = []
    missing_frame: list[str] = []
    missing_prompt = sorted(progress_step_ids - set(prompts))
    for step_id, prompt_index in sorted(prompts.items()):
        sent_index = sent.get(step_id)
        if sent_index is None:
            missing_frame.append(step_id)
        elif prompt_index < sent_index:
            prompt_before_frame.append(step_id)

    evidence = "; ".join(
        (
            "prompts=" + (",".join(sorted(prompts)) if prompts else "none"),
            "sent=" + (",".join(sorted(sent)) if sent else "none"),
            "missing_prompt=" + (",".join(missing_prompt) if missing_prompt else "none"),
            "prompt_before_frame=" + (",".join(prompt_before_frame) if prompt_before_frame else "none"),
            "missing_frame=" + (",".join(missing_frame) if missing_frame else "none"),
        )
    )
    if not missing_prompt and not prompt_before_frame and not missing_frame:
        return _check("lesson_step_prompt_after_frame", evidence, "")

    return {
        "name": "lesson_step_prompt_after_frame",
        "ok": False,
        "evidence": evidence,
        "missing": "completed stepIds must have lesson step prompt/TTS handoff after the outbound lesson_step frame",
    }

def _lesson_step_prompt_after_render_ack_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    sent = _first_indices_matching(lines, scoped, _lesson_step_outbound)
    render_acks = _first_indices_matching(lines, scoped, _lesson_step_rendered_ack)
    prompts = _first_indices_matching_with_current_step_context(lines, scoped, _lesson_step_prompt_handoff)
    audio = _first_indices_matching(lines, scoped, _lesson_audio_played)
    progress_step_ids = _step_ids_matching(lines, scoped, _robot_lesson_progress_success)

    prompt_step_ids = set(prompts)
    missing_prompt = sorted(progress_step_ids - prompt_step_ids)
    missing_frame = sorted(step_id for step_id in prompt_step_ids if step_id not in sent)
    spoken_step_ids = prompt_step_ids | set(audio)
    missing_render_ack = sorted(step_id for step_id in spoken_step_ids if step_id not in render_acks)
    prompt_before_frame = sorted(
        step_id
        for step_id in prompt_step_ids & set(sent)
        if prompts[step_id] < sent[step_id]
    )
    prompt_before_render_ack = sorted(
        step_id
        for step_id in prompt_step_ids & set(render_acks)
        if prompts[step_id] < render_acks[step_id]
    )
    audio_before_render_ack = sorted(
        step_id
        for step_id in set(audio) & set(render_acks)
        if audio[step_id] < render_acks[step_id]
    )

    evidence = "; ".join(
        (
            "prompts=" + (",".join(sorted(prompts)) if prompts else "none"),
            "audio=" + (",".join(sorted(audio)) if audio else "none"),
            "sent=" + (",".join(sorted(sent)) if sent else "none"),
            "render_ack=" + (",".join(sorted(render_acks)) if render_acks else "none"),
            "missing_prompt=" + (",".join(missing_prompt) if missing_prompt else "none"),
            "missing_frame=" + (",".join(missing_frame) if missing_frame else "none"),
            "missing_render_ack=" + (",".join(missing_render_ack) if missing_render_ack else "none"),
            "prompt_before_frame=" + (",".join(prompt_before_frame) if prompt_before_frame else "none"),
            "prompt_before_render_ack=" + (",".join(prompt_before_render_ack) if prompt_before_render_ack else "none"),
            "audio_before_render_ack=" + (",".join(audio_before_render_ack) if audio_before_render_ack else "none"),
        )
    )
    if not (
        missing_prompt
        or missing_frame
        or missing_render_ack
        or prompt_before_frame
        or prompt_before_render_ack
        or audio_before_render_ack
    ):
        return _check("lesson_step_prompt_after_render_ack", evidence, "")

    return {
        "name": "lesson_step_prompt_after_render_ack",
        "ok": False,
        "evidence": evidence,
        "missing": "completed stepIds must have lesson step prompt/TTS handoff and audio only after firmware ACKs the rendered lesson_step",
    }

def _interactive_guided_prompt_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    interactive_step_ids = _interactive_step_ids(lines, scoped)
    progress_step_ids = _step_ids_matching(lines, scoped, _robot_lesson_progress_success)
    guided_prompt_step_ids = _step_ids_matching_with_current_step_context(lines, scoped, _guided_speaking_prompt_handoff)
    guided_prompt_indices = _first_indices_matching_with_current_step_context(lines, scoped, _guided_speaking_prompt_handoff)
    response_indices = _first_indices_matching(lines, scoped, _interactive_child_response_evidence)
    completed_interactive = interactive_step_ids & progress_step_ids
    missing_guided_prompt = sorted(completed_interactive - guided_prompt_step_ids)
    guided_prompt_after_response = sorted(
        step_id
        for step_id in completed_interactive & guided_prompt_step_ids & set(response_indices)
        if guided_prompt_indices.get(step_id, -1) >= response_indices[step_id]
    )

    evidence = "; ".join(
        (
            "interactive=" + (",".join(sorted(interactive_step_ids)) if interactive_step_ids else "none"),
            "progress=" + (",".join(sorted(progress_step_ids)) if progress_step_ids else "none"),
            "guided_prompt=" + (",".join(sorted(guided_prompt_step_ids)) if guided_prompt_step_ids else "none"),
            "missing_guided_prompt=" + (",".join(missing_guided_prompt) if missing_guided_prompt else "none"),
            "guided_prompt_after_response=" + (",".join(guided_prompt_after_response) if guided_prompt_after_response else "none"),
        )
    )
    if not missing_guided_prompt and not guided_prompt_after_response:
        return _check("interactive_guided_prompt", evidence, "")

    return {
        "name": "interactive_guided_prompt",
        "ok": False,
        "evidence": evidence,
        "missing": "completed interactive stepIds must have a guided speaking prompt before child response",
    }

def _lesson_step_content_layers_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    progress_step_ids = _step_ids_matching(lines, scoped, _robot_lesson_progress_success)
    background_step_ids = _step_ids_matching(lines, scoped, _background_rendered_with_media)
    content_step_ids = _step_ids_matching(lines, scoped, _lesson_content_rendered)
    overlay_step_ids = _step_ids_matching(lines, scoped, _robot_overlay_rendered)

    missing_background = progress_step_ids - background_step_ids
    missing_content = progress_step_ids - content_step_ids
    missing_overlay = progress_step_ids - overlay_step_ids
    evidence = "; ".join(
        (
            "progress=" + ",".join(sorted(progress_step_ids)) if progress_step_ids else "progress=none",
            "background=" + ",".join(sorted(background_step_ids)) if background_step_ids else "background=none",
            "content=" + ",".join(sorted(content_step_ids)) if content_step_ids else "content=none",
            "overlay=" + ",".join(sorted(overlay_step_ids)) if overlay_step_ids else "overlay=none",
            "missing_background=" + ",".join(sorted(missing_background)) if missing_background else "missing_background=none",
            "missing_content=" + ",".join(sorted(missing_content)) if missing_content else "missing_content=none",
            "missing_overlay=" + ",".join(sorted(missing_overlay)) if missing_overlay else "missing_overlay=none",
        )
    )
    if not progress_step_ids or not (missing_background or missing_content or missing_overlay):
        return _check("lesson_step_content_layers", evidence, "")

    return {
        "name": "lesson_step_content_layers",
        "ok": False,
        "evidence": evidence,
        "missing": "completed stepIds do not all have backgroundScene, teachingObject, and robotOverlay render evidence",
    }

def _lesson_step_json_layer_step_ids(
    value: Any,
    fallback_step_ids: set[str] | None = None,
) -> tuple[set[str], set[str], set[str]] | None:
    if not isinstance(value, dict) or value.get("type") != "lesson_step":
        return None
    raw_step_id = value.get("stepId") or value.get("step_id")
    step_ids = {str(raw_step_id)} if isinstance(raw_step_id, str) and raw_step_id.strip() else set()
    if not step_ids and fallback_step_ids:
        step_ids = set(fallback_step_ids)
    sources = {path: src for path, src, _expected_key in _lesson_step_required_source_values(value)}
    if not sources:
        return set(), set(), set()

    def declared(path: str) -> set[str]:
        src = sources.get(path)
        return set(step_ids) if isinstance(src, str) and src.strip() else set()

    return (
        declared("backgroundScene.poster.src"),
        declared("teachingObject.asset.src"),
        declared("robotOverlay.asset.src"),
    )

def _lesson_step_declared_layers_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    progress_step_ids = _step_ids_matching(lines, scoped, _robot_lesson_progress_success)
    background_step_ids: set[str] = set()
    teaching_step_ids: set[str] = set()
    overlay_step_ids: set[str] = set()
    for line in lines:
        if not scoped(line) or not _lesson_step_outbound(line):
            continue
        values = _json_values_from_line(line)
        json_steps = [value for value in values if isinstance(value, dict) and value.get("type") == "lesson_step"]
        if json_steps:
            if len(json_steps) != 1:
                continue
            fallback_step_ids = _step_ids_from_evidence(line)
            background_ids, teaching_ids, overlay_ids = _lesson_step_json_layer_step_ids(
                json_steps[0], fallback_step_ids
            ) or (set(), set(), set())
            background_step_ids.update(background_ids)
            teaching_step_ids.update(teaching_ids)
            overlay_step_ids.update(overlay_ids)
            continue
        if "{" in line or "}" in line:
            continue
        lowered = _norm(line)
        step_ids = _step_ids_from_evidence(line)
        if "backgroundscene" in lowered:
            background_step_ids.update(step_ids)
        if "teachingobject" in lowered:
            teaching_step_ids.update(step_ids)
        if "robotoverlay" in lowered:
            overlay_step_ids.update(step_ids)

    missing_background = progress_step_ids - background_step_ids
    missing_teaching = progress_step_ids - teaching_step_ids
    missing_overlay = progress_step_ids - overlay_step_ids
    evidence = "; ".join(
        (
            "progress=" + ",".join(sorted(progress_step_ids)) if progress_step_ids else "progress=none",
            "declared_backgroundScene=" + ",".join(sorted(background_step_ids)) if background_step_ids else "declared_backgroundScene=none",
            "declared_teachingObject=" + ",".join(sorted(teaching_step_ids)) if teaching_step_ids else "declared_teachingObject=none",
            "declared_robotOverlay=" + ",".join(sorted(overlay_step_ids)) if overlay_step_ids else "declared_robotOverlay=none",
            "missing_backgroundScene=" + ",".join(sorted(missing_background)) if missing_background else "missing_backgroundScene=none",
            "missing_teachingObject=" + ",".join(sorted(missing_teaching)) if missing_teaching else "missing_teachingObject=none",
            "missing_robotOverlay=" + ",".join(sorted(missing_overlay)) if missing_overlay else "missing_robotOverlay=none",
        )
    )
    if not progress_step_ids or not (missing_background or missing_teaching or missing_overlay):
        return _check("lesson_step_declares_three_layers", evidence, "")

    return {
        "name": "lesson_step_declares_three_layers",
        "ok": False,
        "evidence": evidence,
        "missing": "completed stepIds must have outbound lesson_step declarations for backgroundScene, teachingObject, and robotOverlay",
    }

def _inline_media_source(source: str) -> bool:
    normalized = source.strip().lower()
    return normalized.startswith("data:") or len(source) > 2048

def _lesson_step_no_inline_media_sources_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    inline_sources: list[str] = []
    for line in lines:
        if not scoped(line) or not _lesson_step_outbound(line):
            continue
        for step_id, _identity, path, src, _expected_key in _lesson_step_source_values(line):
            if isinstance(src, str) and _inline_media_source(src):
                inline_sources.append(f"{step_id}:{path}")

    evidence = "inline_source=" + (",".join(sorted(set(inline_sources))) if inline_sources else "none")
    if not inline_sources:
        return _check("lesson_step_no_inline_media_sources", evidence, "")
    return {
        "name": "lesson_step_no_inline_media_sources",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson_step layer sources must be URL/path references, not inline data/base64 or oversized strings",
    }

def _first_indices_matching(
    lines: list[str],
    scoped: Callable[[str], bool],
    predicate: Callable[[str], bool],
) -> dict[str, int]:
    indices: dict[str, int] = {}
    for index, line in enumerate(lines):
        if not scoped(line) or not predicate(line):
            continue
        for step_id in _step_ids_from_evidence(line):
            indices.setdefault(step_id, index)
    return indices

def _lesson_steps_ordered_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    sent = _first_indices_matching(lines, scoped, _positive_frame("lesson_step"))
    started = _first_indices_matching(lines, scoped, _lesson_step_started)
    rendered = _first_indices_matching(lines, scoped, _background_rendered_with_media)
    content = _first_indices_matching(lines, scoped, _lesson_content_rendered)
    overlay = _first_indices_matching(lines, scoped, _robot_overlay_rendered)
    audio = _first_indices_matching(lines, scoped, _lesson_audio_played)
    ack = _first_indices_matching(lines, scoped, _lesson_step_rendered_ack)
    progress = _first_indices_matching(lines, scoped, _robot_lesson_progress_success)

    out_of_order: list[str] = []
    missing_order_data: list[str] = []
    for step_id, progress_index in sorted(progress.items()):
        required = (
            sent.get(step_id),
            started.get(step_id),
            rendered.get(step_id),
            content.get(step_id),
            overlay.get(step_id),
            audio.get(step_id),
            ack.get(step_id),
        )
        if any(index is None for index in required):
            missing_order_data.append(step_id)
            continue
        sent_index, started_index, rendered_index, content_index, overlay_index, audio_index, ack_index = required
        assert sent_index is not None
        assert started_index is not None
        assert rendered_index is not None
        assert content_index is not None
        assert overlay_index is not None
        assert audio_index is not None
        assert ack_index is not None
        if not (
            sent_index
            <= started_index
            <= rendered_index
            <= content_index
            <= overlay_index
            <= progress_index
            and audio_index <= progress_index
            and ack_index <= progress_index
        ):
            out_of_order.append(step_id)

    evidence = "; ".join(
        (
            "progress=" + ",".join(sorted(progress)) if progress else "progress=none",
            "out_of_order=" + ",".join(out_of_order) if out_of_order else "out_of_order=none",
            "missing_order_data=" + ",".join(missing_order_data) if missing_order_data else "missing_order_data=none",
        )
    )
    if not out_of_order and not missing_order_data:
        return _check("lesson_steps_ordered", evidence, "")

    return {
        "name": "lesson_steps_ordered",
        "ok": False,
        "evidence": evidence,
        "missing": "completed stepIds must be sent, started, rendered, acked, and played before progress",
    }

def _lesson_stop_after_progress_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    stop_index: int | None = None
    stop_predicate = _sent_frame("lesson_stop")
    for index, line in enumerate(lines):
        if scoped(line) and stop_predicate(line):
            stop_index = index
            break

    if stop_index is None:
        return _check("lesson_stop_after_progress", None, "no lesson_stop frame evidence")

    progress_after_stop: list[str] = []
    for line in lines[stop_index + 1 :]:
        if not scoped(line) or not _robot_lesson_progress_success(line):
            continue
        step_ids = sorted(_step_ids_from_evidence(line))
        progress_after_stop.extend(step_ids or ["unknown"])

    evidence = (
        f"stop_index={stop_index}; progress_after_stop=" + ",".join(progress_after_stop)
        if progress_after_stop
        else f"stop_index={stop_index}; progress_after_stop=none"
    )
    if not progress_after_stop:
        return _check("lesson_stop_after_progress", evidence, "")

    return {
        "name": "lesson_stop_after_progress",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson_stop occurred before all successful lesson_progress events",
    }

def _lesson_quiescent_after_stop_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    stop_index: int | None = None
    stop_predicate = _sent_frame("lesson_stop")
    for index, line in enumerate(lines):
        if scoped(line) and stop_predicate(line):
            stop_index = index
            break

    if stop_index is None:
        return _check("lesson_quiescent_after_stop", None, "no lesson_stop frame evidence")

    step_predicate = _positive_frame("lesson_step")
    activity_after_stop: list[str] = []
    for line in lines[stop_index + 1 :]:
        if not scoped(line):
            continue
        has_activity = any(
            predicate(line)
            for predicate in (
                step_predicate,
                _background_rendered,
                _lesson_content_rendered,
                _robot_overlay_rendered,
                _lesson_audio_played,
                _lesson_step_rendered_ack,
                _lesson_progress_success,
                _interactive_child_response_window_opened,
                _interactive_child_response_evidence,
            )
        )
        if not has_activity:
            continue
        step_ids = sorted(_step_ids_from_evidence(line))
        activity_after_stop.extend(step_ids or ["unknown"])

    evidence = (
        f"stop_index={stop_index}; activity_after_stop=" + ",".join(activity_after_stop)
        if activity_after_stop
        else f"stop_index={stop_index}; activity_after_stop=none"
    )
    if not activity_after_stop:
        return _check("lesson_quiescent_after_stop", evidence, "")

    return {
        "name": "lesson_quiescent_after_stop",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson activity occurred after lesson_stop",
    }

def _lesson_quiescent_after_completion_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    completion_index: int | None = None
    completion_predicates = (
        _contains_any(
            "event lesson_completed",
            "post lesson_completed",
            '"type":"lesson_completed"',
            "type=lesson_completed",
            "lesson_completed event",
        ),
        _backend_completion_posted,
        _assignment_completed,
    )
    for index, line in enumerate(lines):
        if not scoped(line):
            continue
        if any(predicate(line) for predicate in completion_predicates):
            completion_index = index

    if completion_index is None:
        return _check("lesson_quiescent_after_completion", None, "no completion evidence")

    activity_after_completion: list[str] = []
    activity_predicates = (
        _positive_frame("lesson_step"),
        _lesson_progress_success,
        _background_rendered,
        _lesson_content_rendered,
        _robot_overlay_rendered,
        _lesson_audio_played,
        _lesson_step_rendered_ack,
        _interactive_child_response_window_opened,
        _interactive_child_response_evidence,
    )
    for line in lines[completion_index + 1 :]:
        if not scoped(line):
            continue
        if not any(predicate(line) for predicate in activity_predicates):
            continue
        step_ids = sorted(_step_ids_from_evidence(line))
        activity_after_completion.extend(step_ids or ["unknown"])

    evidence = (
        f"completion_index={completion_index}; activity_after_completion=" + ",".join(activity_after_completion)
        if activity_after_completion
        else f"completion_index={completion_index}; activity_after_completion=none"
    )
    if not activity_after_completion:
        return _check("lesson_quiescent_after_completion", evidence, "")

    return {
        "name": "lesson_quiescent_after_completion",
        "ok": False,
        "evidence": evidence,
        "missing": "lesson activity occurred after completion",
    }

def _assignment_final_completed_check(lines: list[str], scoped: Callable[[str], bool]) -> dict[str, Any]:
    completion_index: int | None = None
    for index, line in enumerate(lines):
        if scoped(line) and _backend_completion_posted(line):
            completion_index = index
            break

    states: list[str] = []
    states_after_completion: list[str] = []
    valid_after_completion: list[bool] = []
    for index, line in enumerate(lines):
        lowered = _norm(line)
        if not scoped(line):
            continue
        if "assignment/current" not in lowered and "get_current_assignment" not in lowered:
            continue
        if not ASSIGNMENT_ID_PATTERN.search(line):
            continue
        if FAILURE_STATUS_PATTERN.search(line):
            continue
        if any(
            token in lowered
            for token in (
                "fail",
                "failed",
                "failure",
                "error",
                "timeout",
                "completed=false",
                '"completed":false',
                "completed false",
                "complete=false",
                '"complete":false',
                "complete false",
                "success=false",
                '"success":false',
                "success false",
            )
        ):
            continue
        match = ASSIGNMENT_STATE_VALUE_PATTERN.search(line)
        if match:
            state = _norm(match.group(1))
            states.append(state)
            if completion_index is not None and index > completion_index:
                states_after_completion.append(state)
                valid_after_completion.append(_assignment_completed(line))

    if not states:
        return _check("assignment_final_completed", "final_state=unknown; no assignment state evidence", "")

    if completion_index is None:
        evidence = f"states={','.join(states)}; after_completion=none; final_state=unknown"
        return {
            "name": "assignment_final_completed",
            "ok": False,
            "evidence": evidence,
            "missing": "no backend lesson completion post before final assignment state check",
        }

    if not states_after_completion:
        evidence = f"states={','.join(states)}; after_completion=none; final_state=unknown"
        return {
            "name": "assignment_final_completed",
            "ok": False,
            "evidence": evidence,
            "missing": "no backend assignment state evidence after lesson_completed post",
        }

    final_state = states_after_completion[-1]
    final_state_valid = valid_after_completion[-1]
    evidence = (
        f"states={','.join(states)}; "
        f"after_completion={','.join(states_after_completion)}; final_state={final_state}"
    )
    if final_state == "completed" and final_state_valid:
        return _check("assignment_final_completed", evidence, "")

    return {
        "name": "assignment_final_completed",
        "ok": False,
        "evidence": evidence,
        "missing": "final backend assignment state is not completed",
    }

def _first_after(
    lines: list[str],
    start_index: int,
    predicate: Callable[[str], bool],
) -> tuple[int, str | None]:
    for index in range(max(start_index, 0), len(lines)):
        if predicate(lines[index]):
            return index + 1, redact_line(lines[index].strip())
    return start_index, None


def wire_sequence_ranks(lines: list[str]) -> tuple[list[int], dict[int, int]]:
    """Per-line lesson wire sequence, plus where each sequence first appears.

    Lines naming no sequence inherit the last one seen, so setup lines sit at rank -1
    (ahead of frame 1) and a step's render/ack lines stay attached to their frame.
    """
    ranks: list[int] = []
    carried = -1
    for line in lines:
        sequence = _wire_sequence(line)
        if sequence is not None:
            carried = sequence
        ranks.append(carried)
    first_of_rank: dict[int, int] = {}
    for index, rank in enumerate(ranks):
        first_of_rank.setdefault(rank, index)
    return ranks, first_of_rank


class _SequenceCursor:
    """An ordered-checkpoint cursor that advances by WIRE SEQUENCE, not log position.

    Log position cannot order a lesson capture. The two streams are stamped by two
    different clocks (the ESP server in its container, the device on its own board or
    host), and each line is written *after* the work it describes — so a merged capture
    routinely shows the server processing an ack before the device has finished logging
    that it sent one. Measured on a real green run: `preload_ready` and `lesson_started`
    were reported missing purely because the device's ack line landed a few hundred
    microseconds "late", and the same capture scored 81/78/77 across identical runs.

    The `lesson_*` frames already carry a monotonic `sequence`; the server logs it on
    emit and the device echoes it as `seq=`/`acks=`. That is a causal ordering signal,
    immune to clock skew and to which file a line happens to live in.

    Rule: a checkpoint may match any line whose sequence is at or after the current one.
    Matching inside the CURRENT sequence does not push the cursor forward past its other
    lines — within one frame exchange the relative order of two log lines is genuinely
    not recorded, and pretending otherwise is what produced the flapping verdicts.
    Ordering BETWEEN sequences stays strict, which is the ordering the gate is for.
    """

    def __init__(self, lines: list[str]) -> None:
        self._ranks, self._first_of_rank = wire_sequence_ranks(lines)
        self._rank = self._ranks[0] if self._ranks else -1
        self._floor = 0

    def first_after(
        self, lines: list[str], predicate: Callable[[str], bool]
    ) -> str | None:
        for index in range(self._floor, len(lines)):
            if self._ranks[index] < self._rank:
                continue
            if not predicate(lines[index]):
                continue
            self._rank = self._ranks[index]
            # Back to the start of THIS sequence, not past the matched line: the rank
            # guard above is what enforces ordering, and everything inside one sequence
            # must stay reachable.
            self._floor = self._first_of_rank[self._rank]
            return redact_line(lines[index].strip())
        return None


WIRE_SEQUENCE_PATTERNS = (
    re.compile(r'"sequence"\s*:\s*(\d+)'),
    re.compile(r"\bsequence=(\d+)"),
    re.compile(r"\bseq=(\d+)"),
    re.compile(r"\backs=(\d+)"),
)


def _wire_sequence(line: str):
    """Return the lesson wire sequence a line names, if any.

    ``acks=N`` counts: a device ack of frame N belongs immediately after frame N,
    which is exactly the ordering being reconstructed.
    """
    for pattern in WIRE_SEQUENCE_PATTERNS:
        match = pattern.search(line)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
    return None


def order_lines_by_wire_sequence(lines: list[str]) -> list[str]:
    """Re-order a capture by lesson wire sequence instead of by log position.

    The ordered checkpoints walk a monotonic cursor, which assumes the input is in
    event order. Captures are not: the ESP stamps whole seconds, and one real capture
    put 111 lines into 6 distinct timestamps with 51 inside a single second, so log
    position cannot express order and merging two streams by timestamp cannot recover
    it (F-T53-15).

    The ``lesson_*`` frames carry a monotonic ``sequence`` and the device echoes it as
    ``seq=``/``acks=`` -- an ordering signal immune to timestamp resolution. Lines that
    name no sequence inherit the last one seen, so boot/wifi/manifest lines stay ahead
    of the first frame and per-step render lines stay attached to their step. The sort
    is stable, so same-sequence lines keep their original relative order.
    """
    keyed = []
    carried = -1
    for index, line in enumerate(lines):
        sequence = _wire_sequence(line)
        if sequence is not None:
            carried = sequence
        keyed.append((carried, index, line))
    keyed.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in keyed]


def _device_scope(device_id: str, aliases: Iterable[str]) -> Callable[[str], bool]:
    identities = {_norm(identity) for identity in [device_id, *aliases] if identity}

    def scoped(line: str) -> bool:
        # Many captured serial lines are already scoped by file/source and do not carry
        # the MAC or UUID. If a line has an explicit device key, require a match;
        # otherwise accept it as part of the provided log bundle.
        explicit_ids = [match.group(2) for match in EXPLICIT_DEVICE_ID_PATTERN.finditer(line)]
        if explicit_ids:
            return any(_norm(explicit_id) in identities for explicit_id in explicit_ids)
        return True

    return scoped


def _no_default_lesson_fetch_check(
    lines: list[str],
    scoped: Callable[[str], bool],
    start_index: int,
) -> dict[str, Any]:
    """No-assignment scenario: after the start phrase, no default/fallback lesson run may begin.

    Any manifest fetch or outbound lesson frame (lesson_prepare/lesson_start/
    lesson_step) -- or a lesson_started/RUNNING transition -- following the start
    phrase means the robot fell back to a default lesson instead of holding on the
    no-current-assignment status. This is an affirmative-absence gate.
    """
    fetch_predicates = (
        _manifest_fetched_with_identity,
        _positive_frame("lesson_prepare"),
        _positive_frame("lesson_start"),
        _positive_frame("lesson_step"),
        _lesson_started,
    )
    offenders: list[str] = []
    for line in lines[max(start_index, 0):]:
        if not scoped(line):
            continue
        if any(predicate(line) for predicate in fetch_predicates):
            offenders.append(redact_line(line.strip()))

    if not offenders:
        return _check("no_default_lesson_fetch", "no default/fallback lesson manifest-fetch or lesson frames after start phrase", "")
    return {
        "name": "no_default_lesson_fetch",
        "ok": False,
        "evidence": " | ".join(offenders[:5]),
        "missing": "robot fetched a default/fallback lesson after the start phrase despite no current assignment",
    }


def evaluate_no_assignment_logs(
    lines: Iterable[str],
    *,
    device_id: str = DEFAULT_DEVICE_ID,
    device_aliases: Iterable[str] | None = None,
) -> dict[str, Any]:
    """No-assignment evidence validator: backend has no current assignment.

    Given a capture where the active assignment was cleared and the start phrase
    was said, assert the robot surfaced a no-current-assignment status AND did not
    fall back to fetching/starting a default lesson. This is a negative-scenario
    gate (no lesson run should occur), so it is a separate entrypoint from the
    green-path ``evaluate_lesson_logs``.
    """
    materialized = [line.rstrip("\n") for line in lines]
    aliases = list(device_aliases or [])
    scoped = _device_scope(device_id, aliases)
    checks: list[dict[str, Any]] = []

    start_index, start_evidence = _first_after(
        materialized, 0, _and(scoped, _lesson_start_requested)
    )
    checks.append(
        _check(
            "start_lesson_requested",
            start_evidence,
            "no voice/tool start_lesson request evidence",
        )
    )

    _, status_evidence = _first_after(
        materialized, 0, _and(scoped, _no_current_assignment_status)
    )
    checks.append(
        _check(
            "no_current_assignment_status",
            status_evidence,
            "no no-current-assignment status/say evidence after start phrase",
        )
    )

    # Only scope the no-default-fetch window to lines after the start phrase so
    # the gate targets the fallback that this scenario forbids; if the start phrase was
    # never observed, fall back to the whole capture.
    checks.append(_no_default_lesson_fetch_check(materialized, scoped, start_index))

    return {
        "ok": all(check["ok"] for check in checks),
        "device_id": device_id,
        "device_aliases": aliases,
        "line_count": len(materialized),
        "checks": checks,
    }


def _retry_resumes_safely_check(
    lines: list[str],
    scoped: Callable[[str], bool],
) -> dict[str, Any]:
    """Preload recovery: after a preload failure, a retry must reach a clean READY first.

    Given a drop-then-recover capture, find the FIRST preload failure/retry
    marker. From there forward, a clean same-assignment ``preload_ready`` must
    appear BEFORE any ``lesson_start`` frame for that assignment. A ``lesson_start``
    reached without an intervening legitimate READY is the failure shape this
    gate rejects: firmware would start an incomplete lesson. The premise (that a
    failure occurred at all) is asserted by the separate ``preload_failure_observed``
    gate; if no failure marker exists this check is neutral/accepting so it does
    not perturb green-path captures.
    """
    failure_index: int | None = None
    failure_assignments: set[str] = set()
    for index, line in enumerate(lines):
        if not scoped(line) or not _preload_failure_marker(line):
            continue
        failure_index = index
        failure_assignments.update(ASSIGNMENT_ID_PATTERN.findall(line))
        break

    if failure_index is None:
        return _check("retry_resumes_safely", "no preload failure/retry evidence", "")

    # Walk forward from the failure. The first decisive event for the failed
    # assignment wins: a clean same-assignment preload_ready (safe resume) or a
    # lesson_start frame (unsafe — started without re-reaching READY).
    for index in range(failure_index + 1, len(lines)):
        line = lines[index]
        if not scoped(line):
            continue
        line_assignments = set(ASSIGNMENT_ID_PATTERN.findall(line))
        # Only consider events tied to the failed assignment (when the failure
        # named one and the line names an assignment); unlabeled lines are part
        # of the same bundle and remain in scope.
        if (
            failure_assignments
            and line_assignments
            and not (line_assignments & failure_assignments)
        ):
            continue
        if _lesson_preload_ready(line):
            return _check(
                "retry_resumes_safely",
                "recovered=" + redact_line(line.strip()),
                "",
            )
        if _positive_frame("lesson_start")(line):
            return {
                "name": "retry_resumes_safely",
                "ok": False,
                "evidence": "lesson_start before recovery preload_ready: "
                + redact_line(line.strip()),
                "missing": "lesson_start reached after preload failure without an "
                "intervening clean same-assignment preload_ready (firmware would "
                "start an incomplete lesson; retry did not resume safely)",
            }

    return {
        "name": "retry_resumes_safely",
        "ok": False,
        "evidence": "failure=" + redact_line(lines[failure_index].strip()),
        "missing": "preload failed/retried but no later clean same-assignment "
        "preload_ready was observed (retry did not resume to a legitimate READY)",
    }


def evaluate_preload_recovery_logs(
    lines: Iterable[str],
    *,
    device_id: str = DEFAULT_DEVICE_ID,
    device_aliases: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Preload-recovery evidence validator: network loss during preload, retry resumes.

    Given a capture where the internet dropped mid-preload, assert the run held
    (no READY/incomplete start at the failure point) and the retry RESUMED SAFELY:
    a later clean same-assignment ``preload_ready`` preceded any ``lesson_start``.
    The false-READY and incomplete-start halves are already enforced by
    ``_lesson_preload_ready`` and the green-path sequencing; this negative/recovery
    entrypoint adds the remaining "retry resumes safely" clause, so it is separate
    from the green-path ``evaluate_lesson_logs``.
    """
    materialized = [line.rstrip("\n") for line in lines]
    aliases = list(device_aliases or [])
    scoped = _device_scope(device_id, aliases)
    checks: list[dict[str, Any]] = []

    _, start_evidence = _first_after(
        materialized, 0, _and(scoped, _lesson_start_requested)
    )
    checks.append(
        _check(
            "start_lesson_requested",
            start_evidence,
            "no voice/tool start_lesson request evidence",
        )
    )

    _, failure_evidence = _first_after(
        materialized, 0, _and(scoped, _preload_failure_marker)
    )
    checks.append(
        _check(
            "preload_failure_observed",
            failure_evidence,
            "no preload failure/retry evidence (capture is not a network-loss "
            "drop-then-recover run; preload-recovery requires a preload interruption)",
        )
    )

    checks.append(_retry_resumes_safely_check(materialized, scoped))

    return {
        "ok": all(check["ok"] for check in checks),
        "device_id": device_id,
        "device_aliases": aliases,
        "line_count": len(materialized),
        "checks": checks,
    }


def evaluate_lesson_logs(
    lines: Iterable[str],
    *,
    device_id: str = DEFAULT_DEVICE_ID,
    device_aliases: Iterable[str] | None = None,
    expected_lesson_id: str | None = None,
    expected_course_id: str | None = None,
    expected_backend_url: str | None = None,
    expected_child_id: str | None = None,
    expected_device_binding: str | None = None,
    require_lesson_version: bool = False,
    require_assignment_version: bool = False,
    require_story: bool = False,
    require_cp7_sidecar_evidence: bool = False,
    order_by_wire_sequence: bool = False,
) -> dict[str, Any]:
    materialized = [line.rstrip("\n") for line in lines]
    aliases = list(device_aliases or [])
    scoped = _device_scope(device_id, aliases)
    checks: list[dict[str, Any]] = []

    ordered_checks: list[tuple[str, Callable[[str], bool], str]] = [
        (
            "robot_booted",
            _and(scoped, _robot_booted),
            "no robot boot/start evidence",
        ),
        (
            "wifi_connected",
            _and(scoped, _wifi_connected),
            "no robot WiFi connected/IP evidence",
        ),
        (
            "websocket_connected",
            _and(scoped, _websocket_connected),
            "no robot websocket hello/online/session evidence",
        ),
        (
            "lesson_start_requested",
            _and(scoped, _lesson_start_requested),
            "no voice/tool start_lesson request evidence",
        ),
        (
            "lesson_start_acknowledged",
            _and(scoped, _lesson_start_acknowledged),
            "no audible start_lesson acknowledgement evidence",
        ),
        (
            "assignment_current",
            _and(scoped, _active_assignment_current),
            "no active assignment/current evidence",
        ),
        (
            "manifest_fetched",
            _and(scoped, _manifest_fetched_with_identity),
            "no lesson manifest fetch evidence with lesson/course identity",
        ),
        (
            "lesson_prepare_sent",
            _and(scoped, _positive_frame("lesson_prepare")),
            "no lesson_prepare frame evidence",
        ),
        (
            "lesson_prepare_ack",
            _and(scoped, _lesson_ack_positive(1)),
            "no firmware lesson_prepare ack evidence",
        ),
        (
            "lesson_preload_ready",
            _and(scoped, _lesson_preload_ready),
            "no lesson preload/asset-ready evidence",
        ),
        (
            "lesson_start_sent",
            _and(scoped, _positive_frame("lesson_start")),
            "no lesson_start frame evidence",
        ),
        (
            "lesson_start_ack",
            _and(scoped, _lesson_ack_positive(2)),
            "no firmware lesson_start ack evidence",
        ),
        (
            "lesson_started",
            _and(scoped, _lesson_started),
            "no lesson_started/RUNNING evidence after lesson_start ack",
        ),
        (
            "lesson_step_sent",
            _and(scoped, _positive_frame("lesson_step"), _contains_any("stepid", "backgroundscene", "poster", "seq=3", "sequence=3")),
            "no lesson_step frame evidence",
        ),
        (
            "lesson_step_started",
            _and(scoped, _lesson_step_started),
            "no step_started evidence before lesson render/progress",
        ),
        (
            "background_rendered",
            _and(scoped, _background_rendered),
            "no firmware background/poster/video render evidence",
        ),
        (
            "lesson_content_rendered",
            _and(scoped, _lesson_content_rendered),
            "no firmware teachingObject/lesson content render evidence",
        ),
        (
            "robot_overlay_rendered",
            _and(scoped, _robot_overlay_rendered),
            "no firmware robotOverlay/robotState render evidence",
        ),
        (
            "lesson_step_ack",
            _and(scoped, _lesson_step_rendered_ack, _ack_count(3)),
            "no firmware lesson_step ack evidence",
        ),
        (
            "lesson_audio_played",
            _and(scoped, _lesson_audio_played),
            "no lesson audio/TTS playback evidence",
        ),
        (
            "lesson_progress",
            _and(scoped, _robot_lesson_progress_success),
            "no lesson_progress step_completed evidence",
        ),
        (
            "lesson_progress_posted",
            _and(scoped, _backend_progress_posted),
            "no backend lesson_progress post/persist evidence",
        ),
        (
            "lesson_stop_sent",
            _and(scoped, _sent_frame("lesson_stop")),
            "no lesson_stop frame evidence",
        ),
        (
            "lesson_stop_received",
            _and(scoped, _lesson_stop_received),
            "no firmware lesson_stop receive/ack/clear evidence",
        ),
        (
            "lesson_completed",
            _and(scoped, _runtime_lesson_completed),
            "no lesson_completed evidence",
        ),
        (
            "lesson_completion_posted",
            _and(scoped, _backend_completion_posted),
            "no backend lesson completion post/persist evidence",
        ),
        (
            "assignment_completed",
            _and(scoped, _assignment_completed),
            "no backend assignment/course completed state evidence",
        ),
    ]

    cursor = 0
    sequence_cursor = _SequenceCursor(materialized) if order_by_wire_sequence else None
    for name, predicate, missing in ordered_checks:
        if name == "lesson_audio_played":
            _, evidence = _first_after(materialized, 0, predicate)
            checks.append(_check(name, evidence, missing))
            continue
        if sequence_cursor is not None:
            evidence = sequence_cursor.first_after(materialized, predicate)
        else:
            cursor, evidence = _first_after(materialized, cursor, predicate)
        checks.append(_check(name, evidence, missing))
    checks.append(_assignment_consistency_check(checks, materialized, scoped))
    checks.append(_session_consistency_check(checks, materialized, scoped))
    checks.append(_lesson_content_consistency_check(checks, materialized, scoped))
    checks.append(_expected_lesson_identity_check(materialized, scoped, expected_lesson_id))
    checks.append(_expected_course_identity_check(materialized, scoped, expected_course_id))
    checks.append(_expected_backend_url_check(materialized, scoped, expected_backend_url))
    checks.append(_expected_child_identity_check(materialized, scoped, expected_child_id))
    checks.append(_expected_device_binding_check(materialized, scoped, expected_device_binding))
    checks.append(_assignment_version_present_check(materialized, scoped, require_assignment_version))
    checks.append(_lesson_version_present_check(materialized, scoped, require_lesson_version))
    checks.append(_lesson_story_present_check(materialized, scoped, require_story))
    checks.append(_manifest_profile_esp_tft_check(materialized, scoped))
    checks.append(_assignment_manifest_checksum_consistency_check(materialized, scoped))
    checks.append(_lesson_manifest_checksum_consistency_check(materialized, scoped))
    checks.append(_teaching_object_primary_word_consistency_check(checks, materialized, scoped))
    checks.append(_robot_overlay_state_consistency_check(checks, materialized, scoped))
    checks.append(_lesson_audio_primary_word_consistency_check(checks, materialized, scoped))
    checks.append(_render_media_consistency_check(checks, materialized, scoped))
    checks.append(_lesson_step_media_declared_check(checks))
    checks.append(_background_render_media_declared_check(checks))
    checks.append(_step_consistency_check(checks))
    checks.append(_lesson_step_ack_robot_state_check(materialized, scoped))
    checks.append(_lesson_preload_critical_assets_ready_check(materialized, scoped))
    checks.append(_lesson_warm_cache_no_redownload_check(materialized, scoped))
    checks.append(_lesson_asset_pack_keys_present_check(materialized, scoped))
    checks.append(_lesson_asset_pack_no_inline_media_payloads_check(materialized, scoped))
    checks.append(_lesson_asset_pack_required_layer_groups_check(materialized, scoped))
    checks.append(_lesson_asset_pack_ack_ready_check(materialized, scoped))
    checks.append(_lesson_asset_pack_cache_key_matches_manifest_checksum_check(materialized, scoped))
    checks.append(_lesson_asset_pack_sha256_attested_check(materialized, scoped))
    checks.append(_lesson_asset_pack_cache_key_version_segment_check(materialized, scoped))
    checks.append(_lesson_step_sd_pack_sources_attested_check(materialized, scoped))
    checks.append(_lesson_wire_frame_size_budget_check(materialized, scoped))
    checks.append(_lesson_manifest_step_count_consistency_check(materialized, scoped))
    checks.append(_lesson_progress_count_check(materialized, scoped))
    checks.append(_lesson_progress_unique_check(materialized, scoped))
    checks.append(_lesson_step_playback_unique_check(materialized, scoped))
    checks.append(_lesson_sequence_monotonic_check(materialized, scoped))
    checks.append(_lesson_ack_sequence_match_check(materialized, scoped))
    checks.append(_lesson_stop_sequence_match_check(materialized, scoped))
    checks.append(_lesson_progress_sequence_after_step_check(materialized, scoped))
    checks.append(_lesson_stop_sequence_after_progress_check(materialized, scoped))
    checks.append(_lesson_manifest_step_ids_check(materialized, scoped))
    checks.append(_lesson_manifest_step_order_check(materialized, scoped))
    checks.append(_lesson_manifest_completion_classes_check(materialized, scoped))
    checks.append(_lesson_manifest_steps_consistency_check(materialized, scoped))
    checks.append(_lesson_progress_step_identity_check(materialized, scoped))
    checks.append(_lesson_progress_posted_steps_check(materialized, scoped))
    checks.append(_lesson_backend_progress_ordered_check(materialized, scoped))
    checks.append(_lesson_backend_progress_session_check(materialized, scoped))
    checks.append(_lesson_backend_progress_before_stop_check(materialized, scoped))
    checks.append(_lesson_completion_after_backend_progress_check(materialized, scoped))
    checks.append(_lesson_runtime_completion_after_backend_progress_check(materialized, scoped))
    checks.append(_lesson_completion_session_match_check(materialized, scoped))
    checks.append(_lesson_steps_observed_check(materialized, scoped))
    checks.append(_interactive_child_response_window_opened_check(materialized, scoped))
    checks.append(_interactive_child_response_observed_check(materialized, scoped))
    checks.append(_interactive_child_response_ordered_check(materialized, scoped))
    checks.append(_lesson_step_prompt_after_frame_check(materialized, scoped))
    checks.append(_lesson_step_prompt_after_render_ack_check(materialized, scoped))
    checks.append(_interactive_guided_prompt_check(materialized, scoped))
    checks.append(_lesson_no_immediate_pronunciation_scoring_check(materialized, scoped))
    checks.append(_lesson_step_declared_layers_check(materialized, scoped))
    checks.append(_lesson_step_no_inline_media_sources_check(materialized, scoped))
    checks.append(_lesson_step_content_layers_check(materialized, scoped))
    checks.append(_lesson_steps_ordered_check(materialized, scoped))
    checks.append(_lesson_stop_after_progress_check(materialized, scoped))
    checks.append(_lesson_quiescent_after_stop_check(materialized, scoped))
    checks.append(_lesson_quiescent_after_completion_check(materialized, scoped))
    checks.append(_assignment_final_completed_check(materialized, scoped))
    checks.append(_render_not_degraded_check(materialized, scoped))
    checks.append(_render_not_fallback_check(materialized, scoped))
    if require_cp7_sidecar_evidence:
        checks.append(_cp7_log_secret_scrub_check(materialized))
        cp7_run_identity = _cp7_run_identity(materialized, scoped)
        checks.append(
            _sidecar_evidence_check(
                materialized,
                scoped,
                cp7_run_identity,
                "cp7_panel_sidecar_evidence",
                _cp7_panel_sidecar_evidence,
                "no CP-7 panel sidecar evidence for passive+interactive ST77922 three-layer render",
            )
        )
        checks.append(
            _sidecar_evidence_check(
                materialized,
                scoped,
                cp7_run_identity,
                "cp7_conversation_idle_sidecar_evidence",
                _cp7_conversation_idle_sidecar_evidence,
                "no CP-7 sidecar evidence that conversation mode and idle face were restored",
            )
        )
        checks.append(
            _sidecar_evidence_check(
                materialized,
                scoped,
                cp7_run_identity,
                "cp8_alarm_snapshot_sidecar_evidence",
                _cp8_alarm_snapshot_sidecar_evidence,
                "no CP-8 preload alarm snapshot sidecar evidence with p95 and inactive/ok alarm state",
            )
        )
        checks.append(
            _sidecar_evidence_check(
                materialized,
                scoped,
                cp7_run_identity,
                "render_latency_audio_sidecar_evidence",
                _render_latency_audio_sidecar_evidence,
                "no render-latency plus audio-health sidecar evidence for real fetch",
            )
        )
    checks.append(_lesson_start_scoped_to_positive_phrases_check(materialized, scoped))
    checks.append(_fatal_errors_check(materialized, scoped))

    return {
        "ok": all(check["ok"] for check in checks),
        "device_id": device_id,
        "device_aliases": aliases,
        "line_count": len(materialized),
        "checks": checks,
    }


def _republish_asset_pack_identity(
    lines, scoped
):
    """Extract the ready SD asset-pack identity from a single-checksum capture.

    Republish eviction is a cross-run differential (``evaluate_republish_eviction``): each
    capture must carry exactly one ready ``assetPack`` whose ``lessonVersion`` /
    ``manifestChecksum`` / ``cacheKey`` are self-consistent. The ESP server emits
    these via ``core/lesson/asset_cache.py::asset_pack_manifest`` on the
    ``lesson_prepare`` frame. Returns ``ok=False`` when the capture is ambiguous
    (no ready pack, multiple distinct cacheKeys, an invalid/whitespace cacheKey,
    or a cacheKey whose name does not carry its own manifest checksum) so the
    cross-run comparison never reasons over malformed evidence.
    """
    prepare_predicate = _positive_frame("lesson_prepare")
    cache_keys: set[str] = set()
    lesson_versions: set[str] = set()
    checksums: set[str] = set()
    invalid = False
    for line in lines:
        if not scoped(line) or not prepare_predicate(line):
            continue
        for value in _json_values_from_line(line):
            if not isinstance(value, dict) or value.get("type") != "lesson_prepare":
                continue
            body = value.get("body")
            if not isinstance(body, dict):
                continue
            for pack in _asset_pack_values(body):
                if not _truthy(pack.get("ready")):
                    continue
                cache_key, invalid_cache_key = _asset_pack_cache_key_value(pack)
                if invalid_cache_key:
                    invalid = True
                    continue
                if cache_key:
                    cache_keys.add(cache_key)
                raw_version = pack.get("lessonVersion")
                if raw_version is None:
                    raw_version = pack.get("lesson_version")
                if raw_version is not None and not isinstance(raw_version, bool):
                    lesson_versions.add(_norm(str(raw_version)))
                raw_checksum = pack.get("manifestChecksum") or pack.get("manifest_checksum")
                if isinstance(raw_checksum, str) and raw_checksum.strip():
                    checksums.add(_norm(raw_checksum))

    if invalid:
        return {"ok": False, "reason": "invalid_cache_key"}
    if len(cache_keys) != 1 or len(lesson_versions) != 1 or len(checksums) != 1:
        return {
            "ok": False,
            "reason": "ambiguous_or_missing_asset_pack",
            "cache_keys": sorted(cache_keys),
            "lesson_versions": sorted(lesson_versions),
            "checksums": sorted(checksums),
        }
    cache_key = next(iter(cache_keys))
    checksum = next(iter(checksums))
    if checksum not in cache_key:
        return {
            "ok": False,
            "reason": "cache_key_missing_checksum",
            "cache_key": cache_key,
            "checksum": checksum,
        }
    return {
        "ok": True,
        "cache_key": cache_key,
        "lesson_version": next(iter(lesson_versions)),
        "checksum": checksum,
    }


def evaluate_republish_eviction(
    before_lines,
    after_lines,
    *,
    device_id: str = DEFAULT_DEVICE_ID,
    device_aliases=None,
):
    """Validate republish eviction across two single-checksum captures.

    The per-capture ``evaluate_lesson_logs`` consistency gates reject any single
    log mixing two manifest checksums/sessions, so the cross-run intent
    ("checksum changes while lessonVersion stays the SAME, robot EVICTS the old
    SD cache dir and uses a NEW one - no stale images") cannot be asserted inside
    one capture. This evaluator consumes the two single-checksum captures (before
    + after republish), extracts each capture's ready SD asset-pack identity, and
    gates the differential:

      * republish_same_lesson_version - lessonVersion unchanged across the pair
      * republish_checksum_changed    - manifestChecksum actually changed
      * republish_cache_dir_evicted   - cacheKey/cache-dir differs (old dir evicted)
      * republish_no_stale_cache_reuse- after cacheKey carries the new checksum and
                                        does not reuse the before cacheKey
    """
    before_materialized = [line.rstrip("\n") for line in before_lines]
    after_materialized = [line.rstrip("\n") for line in after_lines]
    aliases = list(device_aliases or [])
    scoped = _device_scope(device_id, aliases)

    before = _republish_asset_pack_identity(before_materialized, scoped)
    after = _republish_asset_pack_identity(after_materialized, scoped)

    checks: list[dict[str, Any]] = []

    if not before["ok"] or not after["ok"]:
        evidence = (
            f"before={before.get('reason') or 'ready'}; after={after.get('reason') or 'ready'}"
        )
        missing = (
            "each republish capture must carry exactly one ready SD assetPack with a "
            "self-consistent lessonVersion/manifestChecksum/cacheKey"
        )
        for name in (
            "republish_evidence_present",
            "republish_same_lesson_version",
            "republish_checksum_changed",
            "republish_cache_dir_evicted",
            "republish_no_stale_cache_reuse",
        ):
            checks.append({"ok": False, "name": name, "evidence": evidence, "missing": missing})
        return {
            "ok": False,
            "device_id": device_id,
            "device_aliases": aliases,
            "before_line_count": len(before_materialized),
            "after_line_count": len(after_materialized),
            "checks": checks,
        }

    checks.append(
        _check(
            "republish_evidence_present",
            f"before={before['cache_key']}; after={after['cache_key']}",
            "",
        )
    )

    version_evidence = f"before={before['lesson_version']}; after={after['lesson_version']}"
    if before["lesson_version"] == after["lesson_version"]:
        checks.append(_check("republish_same_lesson_version", version_evidence, ""))
    else:
        checks.append(
            {
                "ok": False,
                "name": "republish_same_lesson_version",
                "evidence": version_evidence,
                "missing": "republish eviction requires lessonVersion to stay the same",
            }
        )

    checksum_evidence = f"before={before['checksum']}; after={after['checksum']}"
    if before["checksum"] != after["checksum"]:
        checks.append(_check("republish_checksum_changed", checksum_evidence, ""))
    else:
        checks.append(
            {
                "ok": False,
                "name": "republish_checksum_changed",
                "evidence": checksum_evidence,
                "missing": "republish eviction requires manifest checksum to change",
            }
        )

    cache_dir_evidence = f"before={before['cache_key']}; after={after['cache_key']}"
    if before["cache_key"] != after["cache_key"]:
        checks.append(_check("republish_cache_dir_evicted", cache_dir_evidence, ""))
    else:
        checks.append(
            {
                "ok": False,
                "name": "republish_cache_dir_evicted",
                "evidence": cache_dir_evidence,
                "missing": "republished content must land in a NEW cache dir; the old cacheKey was reused",
            }
        )

    stale_evidence = (
        f"after_cache_key={after['cache_key']}; after_checksum={after['checksum']}; "
        f"before_cache_key={before['cache_key']}"
    )
    after_carries_new_checksum = after["checksum"] in after["cache_key"]
    reuses_before_dir = after["cache_key"] == before["cache_key"]
    if after_carries_new_checksum and not reuses_before_dir:
        checks.append(_check("republish_no_stale_cache_reuse", stale_evidence, ""))
    else:
        checks.append(
            {
                "ok": False,
                "name": "republish_no_stale_cache_reuse",
                "evidence": stale_evidence,
                "missing": (
                    "after-republish cacheKey must carry the new manifest checksum and must not "
                    "reuse the pre-republish cache dir, so no stale SD images are served"
                ),
            }
        )

    return {
        "ok": all(check["ok"] for check in checks),
        "device_id": device_id,
        "device_aliases": aliases,
        "before_line_count": len(before_materialized),
        "after_line_count": len(after_materialized),
        "checks": checks,
    }

def _read_log_file(path: str) -> list[str]:
    if path == "-":
        return sys.stdin.read().splitlines()
    return Path(path).read_text(encoding="utf-8", errors="replace").splitlines()


def read_log_files(paths: list[str]) -> list[str]:
    lines: list[str] = []
    for path in paths:
        lines.extend(_read_log_file(path))
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify TBOT lesson E2E evidence from ESP-server and firmware serial logs."
    )
    parser.add_argument(
        "--scenario",
        choices=(
            "lesson",
            "manifest-pin-abort",
            "no-assignment",
            "preload-recovery",
            "republish-eviction",
        ),
        default="lesson",
        help=(
            "Evidence scenario to verify. 'lesson' is the normal green-path lesson run; "
            "the other modes validate a manifest-pin abort, no-assignment response, "
            "preload recovery, or cross-run republish eviction."
        ),
    )
    parser.add_argument(
        "--device-id",
        default=DEFAULT_DEVICE_ID,
        help="Wire robot identity used to scope logs (normally the robot MAC address).",
    )
    parser.add_argument(
        "--device-alias",
        action="append",
        default=[],
        help="Additional UUID/MAC identity for the same robot. Can be repeated.",
    )
    parser.add_argument(
        "--log-file",
        action="append",
        default=[],
        help="Read logs from a local file, or '-' for stdin. Can be repeated.",
    )
    parser.add_argument(
        "--before-log-file",
        action="append",
        default=[],
        help="Before-republish log for --scenario republish-eviction. Can be repeated.",
    )
    parser.add_argument(
        "--after-log-file",
        action="append",
        default=[],
        help="After-republish log for --scenario republish-eviction. Can be repeated.",
    )
    parser.add_argument(
        "--expected-lesson-id",
        help="Require scoped lesson evidence to use this wire lesson key.",
    )
    parser.add_argument(
        "--expected-course-id",
        help=(
            "Require the authoritative manifest to use this wire course key; an "
            "assignment/current courseId may be omitted but must not conflict."
        ),
    )
    parser.add_argument(
        "--expected-backend-url",
        help=(
            "Require an explicit apiBase/backendUrl/baseUrl or lesson API endpoint "
            "to use this backend base URL."
        ),
    )
    parser.add_argument(
        "--expected-child-id",
        help="Require the active assignment/current to be bound to this child id.",
    )
    parser.add_argument(
        "--expected-device-binding",
        help=(
            "Require active assignment/current backendDeviceId to match this backend "
            "device UUID; --device-id remains the MAC/wire log scope."
        ),
    )
    parser.add_argument(
        "--require-lesson-version",
        action="store_true",
        help="Require the active assignment/current to carry a lessonVersion.",
    )
    parser.add_argument(
        "--require-assignment-version",
        action="store_true",
        help="Require the active assignment/current to carry an assignmentVersion.",
    )
    parser.add_argument(
        "--require-story",
        action="store_true",
        help="Require captured lesson evidence to include story/storyBeat/narrative metadata.",
    )
    parser.add_argument(
        "--order-by-wire-sequence",
        action="store_true",
        help="Order the capture by lesson wire sequence instead of log position. Use for "
        "real captures: the ESP log stamps whole seconds, so log position cannot express "
        "event order (F-T53-15).",
    )
    parser.add_argument(
        "--require-cp7-sidecar-evidence",
        action="store_true",
        help="Require CP-7 panel, conversation/idle restore, CP-8 snapshot, and render/audio sidecar evidence.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.scenario == "republish-eviction":
            if not args.before_log_file or not args.after_log_file:
                raise ValueError(
                    "--scenario republish-eviction requires --before-log-file and --after-log-file"
                )
            before_lines = read_log_files(args.before_log_file)
            after_lines = read_log_files(args.after_log_file)
            report = evaluate_republish_eviction(
                before_lines,
                after_lines,
                device_id=args.device_id,
                device_aliases=args.device_alias,
            )
            report["sources"] = {
                "before": args.before_log_file,
                "after": args.after_log_file,
            }
        else:
            if not args.log_file:
                raise ValueError(f"--scenario {args.scenario} requires at least one --log-file")
            lines = read_log_files(args.log_file)
            if args.scenario == "no-assignment":
                report = evaluate_no_assignment_logs(
                    lines,
                    device_id=args.device_id,
                    device_aliases=args.device_alias,
                )
            elif args.scenario == "preload-recovery":
                report = evaluate_preload_recovery_logs(
                    lines,
                    device_id=args.device_id,
                    device_aliases=args.device_alias,
                )
            elif args.scenario == "manifest-pin-abort":
                report = evaluate_manifest_pin_abort_logs(
                    lines,
                    device_id=args.device_id,
                    device_aliases=args.device_alias,
                )
            else:
                report = evaluate_lesson_logs(
                    lines,
                    device_id=args.device_id,
                    device_aliases=args.device_alias,
                    expected_lesson_id=args.expected_lesson_id,
                    expected_course_id=args.expected_course_id,
                    expected_backend_url=args.expected_backend_url,
                    expected_child_id=args.expected_child_id,
                expected_device_binding=args.expected_device_binding,
                require_lesson_version=args.require_lesson_version,
                require_assignment_version=args.require_assignment_version,
                require_story=args.require_story,
                require_cp7_sidecar_evidence=args.require_cp7_sidecar_evidence,
                order_by_wire_sequence=args.order_by_wire_sequence,
            )
            report["sources"] = args.log_file
        report["scenario"] = args.scenario
    except Exception as exc:
        report = {
            "ok": False,
            "scenario": getattr(args, "scenario", "lesson"),
            "device_id": args.device_id,
            "device_aliases": args.device_alias,
            "line_count": 0,
            "checks": [],
            "error": str(exc),
            "sources": {
                "log_file": getattr(args, "log_file", []),
                "before": getattr(args, "before_log_file", []),
                "after": getattr(args, "after_log_file", []),
            },
        }
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
