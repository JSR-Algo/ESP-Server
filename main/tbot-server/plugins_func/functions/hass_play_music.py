from plugins_func.register import register_function, ToolType, ActionResponse, Action
from plugins_func.functions.hass_init import initialize_hass_handler
from config.logger import setup_logging
import asyncio
import requests
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

hass_play_music_function_desc = {
    "type": "function",
    "function": {
        "name": "hass_play_music",
        "description": "Use when user wants to listen to music or audiobook; play corresponding audio on media player (media_player) in room",
        "parameters": {
            "type": "object",
            "properties": {
                "media_content_id": {
                    "type": "string",
                    "description": "Can be music or audiobook album name, song name, artist. If unspecified, fill random",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Device id of speaker to operate, entity_id in homeassistant, starts with media_player",
                },
            },
            "required": ["media_content_id", "entity_id"],
        },
    },
}


@register_function(
    "hass_play_music", hass_play_music_function_desc, ToolType.SYSTEM_CTL
)
def hass_play_music(conn: "ConnectionHandler", entity_id="", media_content_id="random"):
    try:
        # Execute music play command
        future = asyncio.run_coroutine_threadsafe(
            handle_hass_play_music(conn, entity_id, media_content_id), conn.loop
        )
        ha_response = future.result()
        return ActionResponse(
            action=Action.RESPONSE, result="Exit intent handled", response=ha_response
        )
    except Exception as e:
        logger.bind(tag=TAG).error(f"Handle music intent error: {e}")


async def handle_hass_play_music(
    conn: "ConnectionHandler", entity_id, media_content_id
):
    ha_config = initialize_hass_handler(conn)
    api_key = ha_config.get("api_key")
    base_url = ha_config.get("base_url")
    url = f"{base_url}/api/services/music_assistant/play_media"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {"entity_id": entity_id, "media_id": media_content_id}
    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        return f"Playing music for {media_content_id}"
    else:
        return f"Music playback failed, error code: {response.status_code}"
