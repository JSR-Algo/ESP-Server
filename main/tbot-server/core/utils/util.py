import re
import os
import json
import copy
import wave
import socket
import asyncio
import requests
import subprocess
import numpy as np
import opuslib_next
from io import BytesIO
from core.utils import p3
from pydub import AudioSegment
from typing import Callable, Any

TAG = __name__


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Connect to Google's DNS servers
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception as e:
        return "127.0.0.1"


def is_private_ip(ip_addr):
    """
    Check if an IP address is a private IP address (compatible with IPv4 and IPv6).

    @param {string} ip_addr - The IP address to check.
    @return {bool} True if the IP address is private, False otherwise.
    """
    try:
        # Validate IPv4 or IPv6 address format
        if not re.match(
            r"^(\d{1,3}\.){3}\d{1,3}$|^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$", ip_addr
        ):
            return False  # Invalid IP address format

        # IPv4 private address ranges
        if "." in ip_addr:  # IPv4 address
            ip_parts = list(map(int, ip_addr.split(".")))
            if ip_parts[0] == 10:
                return True  # 10.0.0.0/8 range
            elif ip_parts[0] == 172 and 16 <= ip_parts[1] <= 31:
                return True  # 172.16.0.0/12 range
            elif ip_parts[0] == 192 and ip_parts[1] == 168:
                return True  # 192.168.0.0/16 range
            elif ip_addr == "127.0.0.1":
                return True  # Loopback address
            elif ip_parts[0] == 169 and ip_parts[1] == 254:
                return True  # Link-local address 169.254.0.0/16
            else:
                return False  # Not a private IPv4 address
        else:  # IPv6 address
            ip_addr = ip_addr.lower()
            if ip_addr.startswith("fc00:") or ip_addr.startswith("fd00:"):
                return True  # Unique Local Addresses (FC00::/7)
            elif ip_addr == "::1":  # pragma: no cover - regex above rejects compressed IPv6
                return True  # Loopback address
            elif ip_addr.startswith("fe80:"):
                return True  # Link-local unicast addresses (FE80::/10)
            else:
                return False  # Not a private IPv6 address

    except (ValueError, IndexError):  # pragma: no cover - regex validation prevents these branches
        return False  # IP address format error or insufficient segments


def get_ip_info(ip_addr, logger):
    try:
        # ImportGlobal cache manager
        from core.utils.cache.manager import cache_manager, CacheType

        # Get from cache first
        cached_ip_info = cache_manager.get(CacheType.IP_INFO, ip_addr)
        if cached_ip_info is not None:
            return cached_ip_info

        # Cache miss, callAPI
        if is_private_ip(ip_addr):
            ip_addr = ""
        url = f"https://whois.pconline.com.cn/ipJson.jsp?json=true&ip={ip_addr}"
        resp = requests.get(url).json()
        ip_info = {"city": resp.get("city")}

        # Store in Cache
        cache_manager.set(CacheType.IP_INFO, ip_addr, ip_info)
        return ip_info
    except Exception as e:
        logger.bind(tag=TAG).error(f"Error getting client ip info: {e}")
        return {}


def write_json_file(file_path, data):
    """Write data to JSON file"""
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def remove_punctuation_and_length(text):
    # Full-width and half-width symbols'UnicodeRange
    full_width_punctuations = (
        "!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~"
    )
    half_width_punctuations = r'!"#$%&\'()*+,-./:;<=>?@[\]^_`{|}~'
    space = " "  # Half-width Space
    full_width_space = "　"  # Full-width Space

    # Remove full-width and half-width symbols and spaces
    result = "".join(
        [
            char
            for char in text
            if char not in full_width_punctuations
            and char not in half_width_punctuations
            and char not in space
            and char not in full_width_space
        ]
    )

    if result == "Yeah":
        return 0, ""
    return len(result), result


def check_model_key(modelType, modelKey):
    if "You" in modelKey:
        return f"Config error: API key for {modelType} not set, current value: {modelKey}"
    return None


def parse_string_to_list(value, separator=";"):
    """
    Convert input value to list
    Args:
        value: input value, can be None, string, or list
        separator: separator, default semicolon
    Returns:
        list: processed list
    """
    if value is None or value == "":
        return []
    elif isinstance(value, str):
        return [item.strip() for item in value.split(separator) if item.strip()]
    elif isinstance(value, list):
        return value
    return []


def check_ffmpeg_installed() -> bool:
    """
    Check whether ffmpeg is correctly installed and executable in current environment.

    Returns:
        bool: Returns True if ffmpeg works normally; otherwise raises ValueError exception.

    Raises:
        ValueError: Raises detailed prompt information when ffmpeg is not installed or dependencies are missing.
    """
    try:
        # Try Execute ffmpeg Command
        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,  # Non-zero exit code triggers CalledProcessError
        )

        output = (result.stdout + result.stderr).lower()
        if "ffmpeg version" in output:
            return True

        # If version not detectedInfo, also treated asExceptionCase
        raise ValueError("No valid ffmpeg version output detected.")

    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        # ExtractErrorOutput
        stderr_output = ""
        if isinstance(e, subprocess.CalledProcessError):
            stderr_output = (e.stderr or "").strip()
        else:
            stderr_output = str(e).strip()

        # Build BasicErrorPrompt
        error_msg = [
            "❌ Detected ffmpeg cannot run normally.\n",
            "Suggest you:",
            "1. Confirm conda environment is correctly activated;",
            "2. See project install docs to learn how to install ffmpeg in conda environment.\n",
        ]

        # 🎯 For SpecificError infoProvide ExtraPrompt
        if "libiconv.so.2" in stderr_output:
            error_msg.append("⚠️ Missing dependency library found: libiconv.so.2")
            error_msg.append("Fix: run in current conda environment:")
            error_msg.append("   conda install -c conda-forge libiconv\n")
        elif (
            "no such file or directory" in stderr_output
            and "ffmpeg" in stderr_output.lower()
        ):
            error_msg.append("⚠️ System did not find ffmpeg executable.")
            error_msg.append("Fix: run in current conda environment:")
            error_msg.append("   conda install -c conda-forge ffmpeg\n")
        else:
            error_msg.append("Error details:")
            error_msg.append(stderr_output or "Unknown error.")

        # Throw DetailedExceptionInfo
        raise ValueError("\n".join(error_msg)) from e


def extract_json_from_string(input_string):
    """Extract JSON part from string"""
    pattern = r"(\{.*\})"
    match = re.search(pattern, input_string, re.DOTALL)  # Add re.DOTALL
    if match:
        return match.group(1)  # Return extracted JSON String
    return None


def audio_to_data_stream(
    audio_file_path, is_opus=True, callback: Callable[[Any], Any] = None, sample_rate=16000, opus_encoder=None
) -> None:
    # Get file extension
    file_type = os.path.splitext(audio_file_path)[1]
    if file_type:
        file_type = file_type.lstrip(".")
    # ReadAudio file,-nostdin Parameter: do not read data from stdin, otherwiseFFmpegWill block
    audio = AudioSegment.from_file(
        audio_file_path, format=file_type, parameters=["-nostdin"]
    )

    # Convert to mono/Specified sample rate/16Little-endian encoding (ensure match encoder)
    audio = audio.set_channels(1).set_frame_rate(sample_rate).set_sample_width(2)

    # Get OriginalPCMData (16bit little-endian)
    raw_data = audio.raw_data
    pcm_to_data_stream(raw_data, is_opus, callback, sample_rate, opus_encoder)


async def audio_to_data(
    audio_file_path: str, is_opus: bool = True, use_cache: bool = True
) -> list[bytes]:
    """
    Convert audio file to Opus/PCM encoded frame list
    Args:
        audio_file_path: audio file path
        is_opus: whether to perform Opus encoding
        use_cache: whether to use cache
    """
    from core.utils.cache.manager import cache_manager
    from core.utils.cache.config import CacheType

    # Generate cache key, includeFile pathand encoding type
    cache_key = f"{audio_file_path}:{is_opus}"

    # Try get result from cache
    if use_cache:
        cached_result = cache_manager.get(CacheType.AUDIO_DATA, cache_key)
        if cached_result is not None:
            return cached_result

    def _sync_audio_to_data():
        # Get file extension
        file_type = os.path.splitext(audio_file_path)[1]
        if file_type:
            file_type = file_type.lstrip(".")
        # ReadAudio file,-nostdin Parameter: do not read data from stdin, otherwiseFFmpegWill block
        audio = AudioSegment.from_file(
            audio_file_path, format=file_type, parameters=["-nostdin"]
        )

        # Convert to mono/16kHzSample rate/16Little-endian encoding (ensure match encoder)
        audio = audio.set_channels(1).set_frame_rate(16000).set_sample_width(2)

        # Get OriginalPCMData (16bit little-endian)
        raw_data = audio.raw_data

        # InitializeOpusEncoder
        encoder = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_AUDIO)

        # Encoding Parameters
        frame_duration = 60  # 60ms per frame
        frame_size = int(16000 * frame_duration / 1000)  # 960 samples/frame

        datas = []
        # Process all audio data by frame (including last frame may be zero-padded)
        for i in range(0, len(raw_data), frame_size * 2):  # 16bit=2bytes/sample
            # Get binary data of current frame
            chunk = raw_data[i : i + frame_size * 2]

            # If last frame insufficient, pad zeros
            if len(chunk) < frame_size * 2:
                chunk += b"\x00" * (frame_size * 2 - len(chunk))

            if is_opus:
                # Convert tonumpyArray Processing
                np_frame = np.frombuffer(chunk, dtype=np.int16)
                # EncodeOpusData
                frame_data = encoder.encode(np_frame.tobytes(), frame_size)
            else:
                frame_data = chunk if isinstance(chunk, bytes) else bytes(chunk)

            datas.append(frame_data)

        return datas

    loop = asyncio.get_running_loop()
    # Execute synchronous audio processing in separate thread
    result = await loop.run_in_executor(None, _sync_audio_to_data)

    # Store result in cache, use defined in configTTL(10Minutes)
    if use_cache:
        cache_manager.set(CacheType.AUDIO_DATA, cache_key, result)

    return result


def audio_bytes_to_data_stream(
    audio_bytes, file_type, is_opus, callback: Callable[[Any], Any], sample_rate=16000, opus_encoder=None
) -> None:
    """
    Directly convert audio binary data to opus/pcm data, supports wav, mp3, p3
    """
    if file_type == "p3":
        # Use directlyp3Decode
        return p3.decode_opus_from_bytes_stream(audio_bytes, callback)
    else:
        # Other formats usepydub
        audio = AudioSegment.from_file(
            BytesIO(audio_bytes), format=file_type, parameters=["-nostdin"]
        )
        audio = audio.set_channels(1).set_frame_rate(sample_rate).set_sample_width(2)
        raw_data = audio.raw_data
        pcm_to_data_stream(raw_data, is_opus, callback, sample_rate, opus_encoder)


def pcm_to_data_stream(raw_data, is_opus=True, callback: Callable[[Any], Any] = None, sample_rate=16000, opus_encoder=None):
    """
    willPCMStream encode data asOpusor direct outputPCM

    Args:
        raw_data: PCMRaw Data
        is_opus: Whether encoded asOpus
        callback: Callback function
        sample_rate: Sample rate
        opus_encoder: OpusEncoderUtilsObject(Recommended to provide to keep encoderStatusContinuous)
    """
    using_temp_encoder = False
    if is_opus and opus_encoder is None:
        encoder = opuslib_next.Encoder(sample_rate, 1, opuslib_next.APPLICATION_AUDIO)
        using_temp_encoder = True

    # Encoding Parameters
    frame_duration = 60  # 60ms per frame
    frame_size = int(sample_rate * frame_duration / 1000)  # samples/frame

    # Process all audio data by frame (including last frame may be zero-padded)
    for i in range(0, len(raw_data), frame_size * 2):  # 16bit=2bytes/sample
        # Get binary data of current frame
        chunk = raw_data[i : i + frame_size * 2]

        # If last frame insufficient, pad zeros
        if len(chunk) < frame_size * 2:
            chunk += b"\x00" * (frame_size * 2 - len(chunk))

        if is_opus:
            if using_temp_encoder:
                # Use temporary encoder (only for independent audio scenario)
                np_frame = np.frombuffer(chunk, dtype=np.int16)
                frame_data = encoder.encode(np_frame.tobytes(), frame_size)
                callback(frame_data)
            else:
                # Use external encoder (TTSStreaming Scenario,KeepStatusContinuous)
                is_last = (i + frame_size * 2 >= len(raw_data))
                opus_encoder.encode_pcm_to_opus_stream(chunk, end_of_stream=is_last, callback=callback)
        else:
            # PCMMode,Output Directly
            frame_data = chunk if isinstance(chunk, bytes) else bytes(chunk)
            callback(frame_data)


def opus_datas_to_wav_bytes(opus_datas, sample_rate=16000, channels=1):
    """
    Decode opus frame list into wav byte stream
    """
    decoder = opuslib_next.Decoder(sample_rate, channels)
    try:
        pcm_datas = []

        frame_duration = 60  # ms
        frame_size = int(sample_rate * frame_duration / 1000)  # 960

        for opus_frame in opus_datas:
            # Decode asPCM(Returnbytes,2Byte/sample points)
            pcm = decoder.decode(opus_frame, frame_size)
            pcm_datas.append(pcm)

        pcm_bytes = b"".join(pcm_datas)

        # WritewavByte stream
        wav_buffer = BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)  # 16bit
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)
        return wav_buffer.getvalue()
    finally:
        if decoder is not None:
            try:
                del decoder
            except Exception:  # pragma: no cover - deleting a local decoder reference should not raise
                pass


def check_vad_update(before_config, new_config):
    selected_module = new_config.get("selected_module")
    if (
        not isinstance(selected_module, dict)
        or selected_module.get("VAD") is None
    ):
        return False
    update_vad = False
    current_vad_module = before_config["selected_module"]["VAD"]
    new_vad_module = selected_module["VAD"]
    current_vad_type = (
        current_vad_module
        if "type" not in before_config["VAD"][current_vad_module]
        else before_config["VAD"][current_vad_module]["type"]
    )
    new_vad_type = (
        new_vad_module
        if "type" not in new_config["VAD"][new_vad_module]
        else new_config["VAD"][new_vad_module]["type"]
    )
    update_vad = current_vad_type != new_vad_type
    return update_vad


def check_asr_update(before_config, new_config):
    selected_module = new_config.get("selected_module")
    if (
        not isinstance(selected_module, dict)
        or selected_module.get("ASR") is None
    ):
        return False
    update_asr = False
    current_asr_module = before_config["selected_module"]["ASR"]
    new_asr_module = selected_module["ASR"]

    # If ModuleNameDifferent, then need update
    if current_asr_module != new_asr_module:
        return True

    # If ModuleNameSame, then compare type
    current_asr_type = (
        current_asr_module
        if "type" not in before_config["ASR"][current_asr_module]
        else before_config["ASR"][current_asr_module]["type"]
    )
    new_asr_type = (
        new_asr_module
        if "type" not in new_config["ASR"][new_asr_module]
        else new_config["ASR"][new_asr_module]["type"]
    )
    update_asr = current_asr_type != new_asr_type
    return update_asr


def filter_sensitive_info(config: dict) -> dict:
    """
    Filter sensitive info in config
    Args:
        config: original config dict
    Returns:
        filtered config dict
    """
    sensitive_keys = [
        "api_key",
        "personal_access_token",
        "access_token",
        "token",
        "secret",
        "access_key_secret",
        "secret_key",
    ]

    def _filter_dict(d: dict) -> dict:
        filtered = {}
        for k, v in d.items():
            if any(sensitive in k.lower() for sensitive in sensitive_keys):
                filtered[k] = "***"
            elif isinstance(v, dict):
                filtered[k] = _filter_dict(v)
            elif isinstance(v, list):
                filtered[k] = [_filter_dict(i) if isinstance(i, dict) else i for i in v]
            elif isinstance(v, str):
                try:
                    json_data = json.loads(v)
                    if isinstance(json_data, dict):
                        filtered[k] = json.dumps(
                            _filter_dict(json_data), ensure_ascii=False
                        )
                    else:
                        filtered[k] = v
                except (json.JSONDecodeError, TypeError):
                    filtered[k] = v
            else:
                filtered[k] = v
        return filtered

    return _filter_dict(copy.deepcopy(config))


def get_vision_url(config: dict) -> str:
    """Get vision URL

    Args:
        config: Config Dictionary

    Returns:
        str: vision URL
    """
    server_config = config["server"]
    vision_explain = server_config.get("vision_explain", "")
    if "Your" in vision_explain:
        local_ip = get_local_ip()
        port = int(server_config.get("http_port", 8003))
        vision_explain = f"http://{local_ip}:{port}/mcp/vision/explain"
    return vision_explain


def is_valid_image_file(file_data: bytes) -> bool:
    """
    Check whether file data is valid image format

    Args:
        file_data: binary file data

    Returns:
        bool: True if valid image format, otherwise False
    """
    # Magic numbers (file headers) of common image formats
    image_signatures = {
        b"\xff\xd8\xff": "JPEG",
        b"\x89PNG\r\n\x1a\n": "PNG",
        b"GIF87a": "GIF",
        b"GIF89a": "GIF",
        b"BM": "BMP",
        b"II*\x00": "TIFF",
        b"MM\x00*": "TIFF",
        b"RIFF": "WEBP",
    }

    # Check whether file header matches any known image format
    for signature in image_signatures:
        if file_data.startswith(signature):
            return True

    return False


def sanitize_tool_name(name: str) -> str:
    """Sanitize tool names for OpenAI compatibility."""
    # SupportChineseChinese, English letters, numbers, underscores, and hyphens
    return re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", name)


def validate_mcp_endpoint(mcp_endpoint: str) -> bool:
    """
    Validate MCP endpoint format

    Args:
        mcp_endpoint: MCP endpoint string

    Returns:
        bool: whether valid
    """
    # 1. Check whether starts withwsStart
    if not mcp_endpoint.startswith("ws"):
        return False

    # 2. Check whether containskey,callWording
    if "key" in mcp_endpoint.lower() or "call" in mcp_endpoint.lower():
        return False

    # 3. Check whether contains/mcp/Wording
    if "/mcp/" not in mcp_endpoint:
        return False

    return True

def get_system_error_response(config: dict) -> str:
    """Get reply for system error

    Args:
        config: config dict

    Returns:
        str: reply for system error
    """
    return config.get("system_error_response", "Master, TBOT is a bit busy now. Let's try again later.")
