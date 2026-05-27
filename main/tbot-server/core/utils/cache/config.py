"""
Cache config management
"""

from enum import Enum
from typing import Dict, Any, Optional
from dataclasses import dataclass
from .strategies import CacheStrategy


class CacheType(Enum):
    """Cache type enum"""

    LOCATION = "location"
    WEATHER = "weather"
    LUNAR = "lunar"
    INTENT = "intent"
    IP_INFO = "ip_info"
    CONFIG = "config"
    DEVICE_PROMPT = "device_prompt"
    VOICEPRINT_HEALTH = "voiceprint_health"  # Voiceprint recognition health check
    AUDIO_DATA = "audio_data"  # Audio data cache


@dataclass
class CacheConfig:
    """Cache config class"""

    strategy: CacheStrategy = CacheStrategy.TTL
    ttl: Optional[float] = 300  # Default5Minute
    max_size: Optional[int] = 1000  # Default Max1000item
    cleanup_interval: float = 60  # Cleanup interval (seconds)

    @classmethod
    def for_type(cls, cache_type: CacheType) -> "CacheConfig":
        """Return preset config by cache type"""
        configs = {
            CacheType.LOCATION: cls(
                strategy=CacheStrategy.TTL, ttl=None, max_size=1000  # Manual Invalidate
            ),
            CacheType.IP_INFO: cls(
                strategy=CacheStrategy.TTL, ttl=86400, max_size=1000  # 24Hour
            ),
            CacheType.WEATHER: cls(
                strategy=CacheStrategy.TTL, ttl=28800, max_size=1000  # 8Hour
            ),
            CacheType.LUNAR: cls(
                strategy=CacheStrategy.TTL, ttl=2592000, max_size=365  # 30Expire in days
            ),
            CacheType.INTENT: cls(
                strategy=CacheStrategy.TTL_LRU, ttl=600, max_size=1000  # 10Minute
            ),
            CacheType.CONFIG: cls(
                strategy=CacheStrategy.FIXED_SIZE, ttl=None, max_size=20  # Manual Invalidate
            ),
            CacheType.DEVICE_PROMPT: cls(
                strategy=CacheStrategy.TTL, ttl=None, max_size=1000  # Manual Invalidate
            ),
            CacheType.VOICEPRINT_HEALTH: cls(
                strategy=CacheStrategy.TTL, ttl=600, max_size=100  # 10Minute Expiry
            ),
            CacheType.AUDIO_DATA: cls(
                strategy=CacheStrategy.TTL, ttl=600, max_size=100  # 10Minute Expiry
            ),
        }
        return configs.get(cache_type, cls())
