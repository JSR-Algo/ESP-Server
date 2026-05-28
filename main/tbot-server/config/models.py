from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import List, Optional, Dict, Any


class AuthConfig(BaseModel):
    enabled: bool = True
    allowed_devices: List[str] = Field(default_factory=list)
    token_expiry_seconds: int = 86400  # 24 hours


class ServerConfig(BaseModel):
    port: int = 8000
    auth: AuthConfig = Field(default_factory=AuthConfig)
    enable_websocket_ping: bool = True
    max_connections: int = 100


class VoiceConfig(BaseModel):
    voice_mode: str = "realtime"
    sample_rate: int = 16000
    frame_duration: int = 60


class TBotConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    server: ServerConfig = Field(default_factory=ServerConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    plugins: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("plugins", mode="before")
    @classmethod
    def check_secrets(cls, v):
        if isinstance(v, dict):
            for key, val in v.items():
                if isinstance(val, str) and val == "__REPLACE_ME__":
                    raise ValueError(
                        f"Plugin secret {key} is not configured (value is __REPLACE_ME__)"
                    )
                if isinstance(val, dict):
                    for sub_key, sub_val in val.items():
                        if isinstance(sub_val, str) and sub_val == "__REPLACE_ME__":
                            raise ValueError(
                                f"Plugin secret {key}.{sub_key} is not configured (value is __REPLACE_ME__)"
                            )
        return v
