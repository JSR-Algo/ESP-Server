import requests
from requests.exceptions import RequestException
from config.logger import setup_logging
from core.providers.llm.base import LLMProviderBase

TAG = __name__
logger = setup_logging()


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        self.agent_id = config.get("agent_id")  # Correspond agent_id
        self.api_key = config.get("api_key")
        self.base_url = config.get("base_url", config.get("url"))  # Default Use base_url
        self.api_url = f"{self.base_url}/api/conversation/process"  # Concatenate complete API URL

    def response(self, session_id, dialogue, **kwargs):
        # home assistantVoice assistant built-in intent, no need usetbot aiBuilt-in, only need pass what user said tohome assistantThen ok

        # Extract last one role for 'user' of content
        input_text = None
        if isinstance(dialogue, list):  # Ensure dialogue is list
            # Traverse in reverse, find last one role for 'user' ofMessage
            for message in reversed(dialogue):
                if message.get("role") == "user":  # Find role for 'user' ofMessage
                    input_text = message.get("content", "")
                    break  # Exit loop immediately after found

        # Construct request data
        payload = {
            "text": input_text,
            "agent_id": self.agent_id,
            "conversation_id": session_id,  # Use session_id As conversation_id
        }
        # SetRequest header
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Initiate POST Request
        with requests.post(self.api_url, json=payload, headers=headers) as response:
            # Check whether request successful
            response.raise_for_status()

            # Parse return data
            data = response.json()
        speech = (
            data.get("response", {})
            .get("speech", {})
            .get("plain", {})
            .get("speech", "")
        )

        # Return generatedContent
        if speech:
            yield speech
        else:
            logger.bind(tag=TAG).warning("API return data has no speech content")

    def response_with_functions(self, session_id, dialogue, functions=None):
        logger.bind(tag=TAG).error(
            f"homeassistant does not support (function call), use other intent recognition"
        )
