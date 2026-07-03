import os
import sys
import errno
import re
from datetime import datetime, timedelta
from urllib.parse import unquote_plus
from loguru import logger
from config.config_loader import get_project_dir, merge_configs, read_config

SERVER_VERSION = "0.9.3"
_logger_initialized = False

_TOKEN_QUERY_RE = re.compile(
    r"(?i)(?:[?&]|\b)(authorization|token|access_token|device_token|websocket_token|ws_token|jwt)=([^&\s\"']+)"
)
_AUTHORIZATION_BEARER_QUERY_RE = re.compile(r"(?i)(?:[?&]|\b)authorization=Bearer(?:\s|$)")
_WS_URL_QUERY_RE = re.compile(r"(?i)(?:[?&]|\b)ws_url=([^&\s\"']+)")
_REDACTED_QUERY_VALUES = {"[redacted]", "redacted", "<redacted>", "***"}
_REDACTED_BEARER_QUERY_RE = re.compile(
    r"(?i)((?:[?&]|\b)authorization=)Bearer\s+(\[redacted\]|redacted|<redacted>|\*\*\*)"
)


def _is_redacted_query_value(value):
    return value.strip().lower() in _REDACTED_QUERY_VALUES


def _normalize_redacted_query_values(text):
    return _REDACTED_BEARER_QUERY_RE.sub(r"\1[REDACTED]", text)


def find_token_leaks_in_access_log(text):
    """Return line/kind entries for token-bearing URL/query access-log leaks."""
    leaks = []
    for line_no, raw_line in enumerate(str(text).splitlines(), start=1):
        line = _normalize_redacted_query_values(unquote_plus(raw_line))
        ws_url_match = _WS_URL_QUERY_RE.search(line)
        if ws_url_match:
            ws_url = ws_url_match.group(1)
            if _AUTHORIZATION_BEARER_QUERY_RE.search(ws_url) or any(
                not _is_redacted_query_value(match.group(2))
                for match in _TOKEN_QUERY_RE.finditer(ws_url)
            ):
                leaks.append({"line": line_no, "kind": "token_bearing_ws_url"})
                continue
        if _AUTHORIZATION_BEARER_QUERY_RE.search(line):
            leaks.append({"line": line_no, "kind": "authorization_query"})
            continue
        if any(
            not _is_redacted_query_value(match.group(2))
            for match in _TOKEN_QUERY_RE.finditer(line)
        ):
            leaks.append({"line": line_no, "kind": "token_query"})
    return leaks


class _SafeFileSink:
    def __init__(self, path, stderr=None, max_bytes=10 * 1024 * 1024, retention_days=30):
        self.path = path
        self.stderr = stderr or sys.stderr
        self.max_bytes = max_bytes
        self.retention_days = retention_days
        self.disabled = False
        self._warned = False
        self._file = None

    def _disable_for_disk_full(self):
        self.disabled = True
        if not self._warned:
            self._warned = True
            print(
                f"WARNING: file logging disabled: no space left on device ({self.path})",
                file=self.stderr,
            )

    def _close_file(self):
        if self._file is not None and not self._file.closed:
            self._file.close()
        self._file = None

    def _cleanup_old_logs(self):
        if self.retention_days is None:
            return
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        directory = os.path.dirname(self.path) or "."
        prefix = os.path.basename(self.path) + "."
        for name in os.listdir(directory):
            if not name.startswith(prefix):
                continue
            candidate = os.path.join(directory, name)
            try:
                if datetime.fromtimestamp(os.path.getmtime(candidate)) < cutoff:
                    os.remove(candidate)
            except OSError:
                pass

    def _rotate_if_needed(self, message_len):
        if not self.max_bytes or not os.path.exists(self.path):
            return
        if os.path.getsize(self.path) + message_len <= self.max_bytes:
            return
        self._close_file()
        suffix = datetime.now().strftime("%Y%m%d%H%M%S")
        os.replace(self.path, f"{self.path}.{suffix}")
        self._cleanup_old_logs()

    def __call__(self, message):
        if self.disabled:
            return
        try:
            text = str(message)
            self._rotate_if_needed(len(text.encode("utf-8")))
            if self._file is None or self._file.closed:
                self._file = open(self.path, "a", encoding="utf-8")
            self._file.write(text)
            self._file.flush()
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                self._close_file()
                self._disable_for_disk_full()
                return
            raise


def _load_log_config():
    project_dir = get_project_dir()
    default_config = read_config(os.path.join(project_dir, "config.yaml"))
    custom_config_path = os.path.join(project_dir, "data/.config.yaml")
    custom_config = (
        read_config(custom_config_path) if os.path.exists(custom_config_path) else {}
    )
    config = merge_configs(default_config or {}, custom_config or {})
    return config.get("log", {}) or {}


def get_module_abbreviation(module_name, module_dict):
    """获取模块名称的缩写，如果为空则返回00
    如果名称中包含下划线，则返回下划线后面的前两个字符
    """
    module_value = module_dict.get(module_name, "")
    if not module_value:
        return "00"
    if "_" in module_value:
        parts = module_value.split("_")
        return parts[-1][:2] if parts[-1] else "00"
    return module_value[:2]


def build_module_string(selected_module):
    """构建模块字符串"""
    return (
        get_module_abbreviation("VAD", selected_module)
        + get_module_abbreviation("ASR", selected_module)
        + get_module_abbreviation("LLM", selected_module)
        + get_module_abbreviation("TTS", selected_module)
        + get_module_abbreviation("Memory", selected_module)
        + get_module_abbreviation("Intent", selected_module)
        + get_module_abbreviation("VLLM", selected_module)
    )


def formatter(record):
    """为没有 tag 的日志添加默认值，并处理动态模块字符串"""
    record["extra"].setdefault("tag", record["name"])
    # 如果没有设置 selected_module，使用默认值
    record["extra"].setdefault("selected_module", "00000000000000")
    # 将 selected_module 从 extra 提取到顶级，以支持 {selected_module} 格式
    record["selected_module"] = record["extra"]["selected_module"]
    return record["message"]


def setup_logging():
    """从配置文件中读取日志配置，并设置日志输出格式和级别"""
    log_config = _load_log_config()
    global _logger_initialized

    # 第一次初始化时配置日志
    if not _logger_initialized:
        # 使用默认的模块字符串进行初始化
        logger.configure(
            extra={
                "selected_module": log_config.get("selected_module", "00000000000000"),
            }
        )

        log_format = log_config.get(
            "log_format",
            "<green>{time:YYMMDD HH:mm:ss}</green>[{version}_{extra[selected_module]}][<light-blue>{extra[tag]}</light-blue>]-<level>{level}</level>-<light-green>{message}</light-green>",
        )
        log_format_file = log_config.get(
            "log_format_file",
            "{time:YYYY-MM-DD HH:mm:ss} - {version}_{extra[selected_module]} - {name} - {level} - {extra[tag]} - {message}",
        )
        log_format = log_format.replace("{version}", SERVER_VERSION)
        log_format_file = log_format_file.replace("{version}", SERVER_VERSION)

        log_level = log_config.get("log_level", "INFO")
        log_dir = log_config.get("log_dir", "tmp")
        log_file = log_config.get("log_file", "server.log")
        data_dir = log_config.get("data_dir", "data")

        os.makedirs(log_dir, exist_ok=True)
        os.makedirs(data_dir, exist_ok=True)

        # 配置日志输出
        logger.remove()

        # 输出到控制台
        logger.add(sys.stdout, format=log_format, level=log_level, filter=formatter)

        # 输出到文件 - 统一目录，按大小轮转
        # 日志文件完整路径
        log_file_path = os.path.join(log_dir, log_file)

        # 添加日志处理器，磁盘满时保留控制台日志
        logger.add(
            _SafeFileSink(log_file_path),
            format=log_format_file,
            level=log_level,
            filter=formatter,
            enqueue=True,
            backtrace=False,
            diagnose=False,
        )
        _logger_initialized = True  # 标记为已初始化

    return logger


def create_connection_logger(selected_module_str):
    """为连接创建独立的日志器，绑定特定的模块字符串"""
    return logger.bind(selected_module=selected_module_str)
