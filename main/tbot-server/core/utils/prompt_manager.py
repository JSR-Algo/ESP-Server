"""
System prompt manager module
Manages and updates system prompts, including quick init and async enhancement
"""

import os
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from config.logger import setup_logging
from jinja2 import Template

TAG = __name__

WEEKDAY_MAP = {
    "Monday": "Monday",
    "Tuesday": "Tuesday",
    "Wednesday": "Wednesday",
    "Thursday": "Thursday",
    "Friday": "Friday",
    "Saturday": "Saturday",
    "Sunday": "Sunday",
}

EMOJI_List = [
    "😶",
    "🙂",
    "😆",
    "😂",
    "😔",
    "😠",
    "😭",
    "😍",
    "😳",
    "😲",
    "😱",
    "🤔",
    "😉",
    "😎",
    "😌",
    "🤤",
    "😘",
    "😏",
    "😴",
    "😜",
    "🙄",
]


class PromptManager:
    """System prompt manager, responsible for managing and updating system prompts"""

    def __init__(self, config: Dict[str, Any], logger=None):
        self.config = config
        self.logger = logger or setup_logging()
        self.base_prompt_template = None
        self.last_update_time = 0

        # ImportGlobal cache manager
        from core.utils.cache.manager import cache_manager, CacheType

        self.cache_manager = cache_manager
        self.CacheType = CacheType

        # Init context source
        from core.utils.context_provider import ContextDataProvider

        self.context_provider = ContextDataProvider(config, self.logger)
        self.context_data = {}

        self._load_base_template()

    def _load_base_template(self):
        """Load base prompt template"""
        try:
            template_path = self.config.get("prompt_template", None)
            if not template_path:
                template_path = "agent-base-prompt.txt"
            cache_key = f"prompt_template:{template_path}"

            # Get from cache first
            cached_template = self.cache_manager.get(self.CacheType.CONFIG, cache_key)
            if cached_template is not None:
                self.base_prompt_template = cached_template
                self.logger.bind(tag=TAG).debug("Load base prompt template from cache")
                return

            # Cache miss, read from file
            if os.path.exists(template_path):
                with open(template_path, "r", encoding="utf-8") as f:
                    template_content = f.read()

                # Store in cache (CONFIGType does not auto expire by default, need manual invalidation)
                self.cache_manager.set(
                    self.CacheType.CONFIG, cache_key, template_content
                )
                self.base_prompt_template = template_content
                self.logger.bind(tag=TAG).debug("Successfully loaded base prompt template and cached")
            else:
                self.logger.bind(tag=TAG).warning(f"{template_path} file not found")
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Load prompt template failed: {e}")

    def get_quick_prompt(self, user_prompt: str, device_id: str = None) -> str:
        """Quickly get system prompt (using user config)"""
        device_cache_key = f"device_prompt:{device_id}"
        cached_device_prompt = self.cache_manager.get(
            self.CacheType.DEVICE_PROMPT, device_cache_key
        )
        if cached_device_prompt is not None:
            self.logger.bind(tag=TAG).debug(f"Use cached prompt for device {device_id}")
            return cached_device_prompt
        else:
            self.logger.bind(tag=TAG).debug(
                f"Device {device_id} has no cached prompt, use incoming prompt"
            )

        # Use passed inPromptwords and cache (if anyDevice ID)
        if device_id:
            device_cache_key = f"device_prompt:{device_id}"
            self.cache_manager.set(self.CacheType.DEVICE_PROMPT, device_cache_key, user_prompt)
            self.logger.bind(tag=TAG).debug(f"Prompt for device {device_id} cached")

        self.logger.bind(tag=TAG).info(f"Use quick prompt: {user_prompt[:50]}...")
        return user_prompt

    def _get_current_time_info(self) -> tuple:
        """Get current time info"""
        from .current_time import (
            get_current_date,
            get_current_weekday,
            get_current_lunar_date,
        )

        today_date = get_current_date()
        today_weekday = get_current_weekday()
        lunar_date = get_current_lunar_date() + "\n"

        return today_date, today_weekday, lunar_date

    def _get_location_info(self, client_ip: str) -> str:
        """Get location info"""
        try:
            # Get from cache first
            cached_location = self.cache_manager.get(self.CacheType.LOCATION, client_ip)
            if cached_location is not None:
                return cached_location

            # Cache miss, callAPIGet
            from core.utils.util import get_ip_info

            ip_info = get_ip_info(client_ip, self.logger)
            city = ip_info.get("city", "Unknown location")
            location = f"{city}"

            # Store in Cache
            self.cache_manager.set(self.CacheType.LOCATION, client_ip, location)
            return location
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Get location info failed: {e}")
            return "Unknown location"

    def _get_weather_info(self, conn: "ConnectionHandler", location: str) -> str:
        """Get weather info"""
        try:
            # Get from cache first
            cached_weather = self.cache_manager.get(self.CacheType.WEATHER, location)
            if cached_weather is not None:
                return cached_weather

            # Cache miss, callget_weatherFunction Get
            from plugins_func.functions.get_weather import get_weather
            from plugins_func.register import ActionResponse

            # Callget_weatherFunction
            result = get_weather(conn, location=location, lang="zh_CN")
            if isinstance(result, ActionResponse):
                weather_report = result.result
                self.cache_manager.set(self.CacheType.WEATHER, location, weather_report)
                return weather_report
            return "Get weather info failed"

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Get weather info failed: {e}")
            return "Get weather info failed"

    def update_context_info(self, conn, client_ip: str):
        """Synchronously update context info"""
        try:
            local_address = ""
            if (
                client_ip
                and self.base_prompt_template
                and (
                    "local_address" in self.base_prompt_template
                    or "weather_info" in self.base_prompt_template
                )
            ):
                # Get location info(use global cache)
                local_address = self._get_location_info(client_ip)

            if (
                self.base_prompt_template
                and "weather_info" in self.base_prompt_template
                and local_address
            ):
                # Get weather info(use global cache)
                self._get_weather_info(conn, local_address)

            # Get configured context data
            if hasattr(conn, "device_id") and conn.device_id:
                if (
                    self.base_prompt_template
                    and "dynamic_context" in self.base_prompt_template
                ):
                    self.context_data = self.context_provider.fetch_all(conn.device_id)
                else:
                    self.context_data = ""

            self.logger.bind(tag=TAG).debug(f"Context info update complete")

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Update context info failed: {e}")

    def build_enhanced_prompt(
        self, user_prompt: str, device_id: str, client_ip: str = None, *args, **kwargs
    ) -> str:
        """Build enhanced system prompt"""
        if not self.base_prompt_template:
            return user_prompt

        try:
            # Get latest timeInfo(no cache)
            today_date, today_weekday, lunar_date = self._get_current_time_info()

            # Get cached contextInfo
            local_address = ""
            weather_info = ""

            if client_ip:
                # Get location info(From global cache)
                local_address = (
                    self.cache_manager.get(self.CacheType.LOCATION, client_ip) or ""
                )

                # Get weather info(From global cache)
                if local_address:
                    weather_info = (
                        self.cache_manager.get(self.CacheType.WEATHER, local_address)
                        or ""
                    )

            # GetTTSSelectedLanguage, default value isChinese
            selected_module = self.config.get("selected_module") or {}
            if not isinstance(selected_module, dict):
                selected_module = {}
            tts_config = self.config.get("TTS") or {}
            if not isinstance(tts_config, dict):
                tts_config = {}
            language = (
                tts_config
                .get(selected_module.get("TTS", ""), {})
                .get("language")
                or "Chinese"
            )
            self.logger.bind(tag=TAG).debug(f"Got selected language: {language}")
            child_profile = self._normalize_child_profile(
                kwargs.pop("child_profile", None) or self.config.get("child_profile")
            )

            # Replace template variables
            template = Template(self.base_prompt_template)
            enhanced_prompt = template.render(
                base_prompt=user_prompt,
                current_time="{{current_time}}",
                today_date=today_date,
                today_weekday=today_weekday,
                lunar_date=lunar_date,
                local_address=local_address,
                weather_info=weather_info,
                emojiList=EMOJI_List,
                device_id=device_id,
                client_ip=client_ip,
                dynamic_context=self.context_data,
                language=language,
                child_profile=child_profile,
                *args,
                **kwargs,
            )
            device_cache_key = f"device_prompt:{device_id}"
            self.cache_manager.set(
                self.CacheType.DEVICE_PROMPT, device_cache_key, enhanced_prompt
            )
            self.logger.bind(tag=TAG).info(
                f"Enhanced prompt built successfully, length: {len(enhanced_prompt)}"
            )
            return enhanced_prompt

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Build enhanced prompt failed: {e}")
            return user_prompt

    def _normalize_child_profile(self, child_profile: Any) -> Dict[str, Any]:
        """Return safe child profile fields for prompt rendering."""
        if not isinstance(child_profile, dict):
            return {}

        normalized = {}
        for source_key, target_key in (
            ("device_id", "device_id"),
            ("device_alias", "device_alias"),
            ("child_name", "child_name"),
            ("child_age", "child_age"),
            ("interests", "interests"),
            ("learning_style", "learning_style"),
            ("vocabulary_level", "vocabulary_level"),
            ("parent_career", "parent_career"),
            ("childName", "child_name"),
            ("childAge", "child_age"),
            ("deviceAlias", "device_alias"),
            ("learningStyle", "learning_style"),
            ("vocabularyLevel", "vocabulary_level"),
            ("parentCareer", "parent_career"),
        ):
            value = child_profile.get(source_key)
            if value not in (None, ""):
                normalized[target_key] = value
        return normalized
