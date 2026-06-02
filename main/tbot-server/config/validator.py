from .models import TBotConfig


def validate_config_or_die(config: dict) -> TBotConfig:
    try:
        return TBotConfig(**config)
    except Exception as e:
        raise SystemExit(f"Config validation failed: {e}")
