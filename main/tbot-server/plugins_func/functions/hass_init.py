from config.logger import setup_logging
from core.utils.util import check_model_key

TAG = __name__
logger = setup_logging()


def append_devices_to_prompt(conn):
    if conn.intent_type == "function_call":
        funcs = conn.config["Intent"][conn.config["selected_module"]["Intent"]].get(
            "functions", []
        )

        # Safely get plugin config
        plugins_config = conn.config.get("plugins", {})
        config_source = (
            "home_assistant"
            if plugins_config.get("home_assistant")
            else "hass_get_state"
        )

        if "hass_get_state" in funcs or "hass_set_state" in funcs:
            prompt = "\nBelow is my home smart device list (location, device name, entity_id), controllable through homeassistant\n"
            deviceStr = plugins_config.get(config_source, {}).get("devices", "")
            conn.prompt += prompt + deviceStr + "\n"
            # UpdatePromptword
            conn.dialogue.update_system_message(conn.prompt)


def initialize_hass_handler(conn):
    ha_config = {}
    if not conn.load_function_plugin:
        return ha_config

    # Safely get plugin config
    plugins_config = conn.config.get("plugins", {})
    # Confirm ConfigSource
    config_source = (
        "home_assistant" if plugins_config.get("home_assistant") else "hass_get_state"
    )
    if not plugins_config.get(config_source):
        return ha_config

    # Unified get config
    plugin_config = plugins_config[config_source]
    ha_config["base_url"] = plugin_config.get("base_url")
    ha_config["api_key"] = plugin_config.get("api_key")

    # Unified CheckAPI key
    model_key_msg = check_model_key("home_assistant", ha_config.get("api_key"))
    if model_key_msg:
        logger.bind(tag=TAG).error(model_key_msg)

    return ha_config
