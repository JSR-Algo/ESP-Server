from typing import List, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from ..base import IntentProviderBase
from plugins_func.functions.play_music import initialize_music_handler
from config.logger import setup_logging
from core.utils.util import get_system_error_response
import re
import json
import hashlib
import time



TAG = __name__
logger = setup_logging()


class IntentProvider(IntentProviderBase):
    def __init__(self, config):
        super().__init__(config)
        self.llm = None
        self.promot = ""
        # ImportGlobal cache manager
        from core.utils.cache.manager import cache_manager, CacheType

        self.cache_manager = cache_manager
        self.CacheType = CacheType
        self.history_count = 4  # Default use latest4dialog records

    def get_intent_system_prompt(self, functions_list: str) -> str:
        """
        Dynamically generate system prompt based on configured intent options and available functions
        Args:
            functions: available function list, JSON format string
        Returns:
            formatted system prompt
        """

        # Build function description part
        functions_desc = "Available function list:\n"
        for func in functions_list:
            func_info = func.get("function", {})
            name = func_info.get("name", "")
            desc = func_info.get("description", "")
            params = func_info.get("parameters", {})

            functions_desc += f"\nFunction name: {name}\n"
            functions_desc += f"Description: {desc}\n"

            if params:
                functions_desc += "Parameters:\n"
                for param_name, param_info in params.get("properties", {}).items():
                    param_desc = param_info.get("description", "")
                    param_type = param_info.get("type", "")
                    functions_desc += f"- {param_name} ({param_type}): {param_desc}\n"

            functions_desc += "---\n"

        prompt = (
            "[Strict format requirement] You must return JSON format only, absolutely no natural language!\n\n"
            "You are an intent recognition assistant. Analyze user's last sentence, determine user intent, and call corresponding function.\n\n"
            "[Important Rule]For following query types, return result_for_context directly, no function call needed:\n"
            "- Ask current time (such as: what time is it now, current time, query time, etc.)\n"
            "- Ask today’s date (such as: what date is it today, what day of week is it, what is today’s date, etc.)\n"
            "- Ask today’s lunar date (such as: what lunar date is it today, what solar term is today, etc.)\n"
            "- Ask current city (such as: where am I now, do you know which city I’m in, etc.)"
            "System builds answer directly from context.\n\n"
            "- If user uses question words (such as'What','Why','How' ) ask exit-related questions (for example'Why exit?'), note this is not to letYouExit, please return {'function_call': {'name': 'continue_chat'}\n"
            "- Trigger only when user explicitly uses'Exit system','End conversation','I don't want to talk to you'and other commands handle_exit_intent\n\n"
            f"{functions_desc}\n"
            "Processing Steps:\n"
            "1. Analyze user input, determine user intent\n"
            "2. Check whether above baseInfoQuery (time, date, etc.), if yes returnresult_for_context\n"
            "3. Choose best-matching function from available function list\n"
            "4. If matching function found, generate correspondingfunction_call Format\n"
            '5. If no matching function found, return{"function_call": {"name": "continue_chat"}}\n\n'
            "Return format requirements:\n"
            "1. Must return pure JSON format, do not include any other text\n"
            "2. Must include function_call field\n"
            "3. function_call must include name field\n"
            "4. If function needs parameters, must include arguments field\n\n"
            "Example:\n"
            "```\n"
            "User: What time is it now?\n"
            'Return: {"function_call": {"name": "result_for_context"}}\n'
            "```\n"
            "```\n"
            "User: What is current battery level?\n"
            'Return: {"function_call": {"name": "get_battery_level", "arguments": {"response_success": "Current battery level is {value}%", "response_failure": "Unable to get current Battery percentage"}}}\n'
            "```\n"
            "```\n"
            "User: What is current screen brightness?\n"
            'Return: {"function_call": {"name": "self_screen_get_brightness"}}\n'
            "```\n"
            "```\n"
            "User: Set screen brightness to50%\n"
            'Return: {"function_call": {"name": "self_screen_set_brightness", "arguments": {"brightness": 50}}}\n'
            "```\n"
            "```\n"
            "User: I wantEnd conversation\n"
            'Return: {"function_call": {"name": "handle_exit_intent", "arguments": {"say_goodbye": "goodbye"}}}\n'
            "```\n"
            "```\n"
            "User: YouOK\n"
            'Return: {"function_call": {"name": "continue_chat"}}\n'
            "```\n\n"
            "Note:\n"
            "1. Return onlyJSONFormat, do not include any other text\n"
            '2. Check FirstUser queryWhether basicInfo(time, date, etc.), if yes return{"function_call": {"name": "result_for_context"}}, no needargumentsParameter\n'
            '3. If no matching function found, return{"function_call": {"name": "continue_chat"}}\n'
            "4. Ensure returned JSON format is correct and includes all required fields\n"
            "5. result_for_context needs no parameters. System automatically gets info from context\n"
            "Special notes:\n"
            "- When user single input contains multiple commands (such as'Turn on light and increase volume')\n"
            "- Please return multiplefunction_callComposed ofJSONArray\n"
            "- Example:{'function_calls': [{name:'light_on'}, {name:'volume_up'}]}\n\n"
            "[Final warning]Absolutely forbid output any naturalLanguage, emoji, or explanatory text! Output only validJSONFormat! Violating this rule will cause systemError!"
        )
        return prompt

    def replyResult(self, text: str, original_text: str):
        try:
            llm_result = self.llm.response_no_stream(
                system_prompt=text,
                user_prompt="Please based on aboveContent, reply to user in human-like speaking tone. Must be concise. Return result directly. User now says:"
                + original_text,
            )
            return llm_result
        except Exception as e:
            logger.bind(tag=TAG).error(f"Error in generating reply result: {e}")
            return get_system_error_response(self.config)

    async def detect_intent(
        self, conn: "ConnectionHandler", dialogue_history: List[Dict], text: str
    ) -> str:
        if not self.llm:
            raise ValueError("LLM provider not set")
        if conn.func_handler is None:
            return '{"function_call": {"name": "continue_chat"}}'

        # Record overall start time
        total_start_time = time.time()

        # Print used modelInfo
        model_info = getattr(self.llm, "model_name", str(self.llm.__class__.__name__))
        logger.bind(tag=TAG).debug(f"Use intent recognition model: {model_info}")

        # Calculate cache key
        cache_key = hashlib.md5((conn.device_id + text).encode()).hexdigest()

        # Check Cache
        cached_intent = self.cache_manager.get(self.CacheType.INTENT, cache_key)
        if cached_intent is not None:
            cache_time = time.time() - total_start_time
            logger.bind(tag=TAG).debug(
                f"Using cached intent: {cache_key} -> {cached_intent}, time: {cache_time:.4f}s"
            )
            return cached_intent

        if self.promot == "":
            functions = conn.func_handler.get_functions()
            if hasattr(conn, "mcp_client"):
                mcp_tools = conn.mcp_client.get_available_tools()
                if mcp_tools is not None and len(mcp_tools) > 0:
                    if functions is None:
                        functions = []
                    functions.extend(mcp_tools)

            self.promot = self.get_intent_system_prompt(functions)

        music_config = initialize_music_handler(conn)
        music_file_names = music_config["music_file_names"]
        prompt_music = f"{self.promot}\n<musicNames>{music_file_names}\n</musicNames>"

        home_assistant_cfg = conn.config["plugins"].get("home_assistant")
        if home_assistant_cfg:
            devices = home_assistant_cfg.get("devices", [])
        else:
            devices = []
        if len(devices) > 0:
            hass_prompt = "\nBelow is my home smart device list (location, device name, entity_id), controllable through homeassistant\n"
            for device in devices:
                hass_prompt += device + "\n"
            prompt_music += hass_prompt

        logger.bind(tag=TAG).debug(f"User prompt: {prompt_music}")

        # Build user chat historyPrompt
        msgStr = ""

        # Get recent chat history
        start_idx = max(0, len(dialogue_history) - self.history_count)
        for i in range(start_idx, len(dialogue_history)):
            msgStr += f"{dialogue_history[i].role}: {dialogue_history[i].content}\n"

        msgStr += f"User: {text}\n"
        user_prompt = f"current dialogue:\n{msgStr}"

        # Record preprocessing completion time
        preprocess_time = time.time() - total_start_time
        logger.bind(tag=TAG).debug(f"Intent recognition preprocessing time: {preprocess_time:.4f}s")

        # UseLLMPerform intent recognition
        llm_start_time = time.time()
        logger.bind(tag=TAG).debug(f"Start LLM intent recognition call, model: {model_info}")

        try:
            intent = self.llm.response_no_stream(
                system_prompt=prompt_music, user_prompt=user_prompt
            )
        except Exception as e:
            logger.bind(tag=TAG).error(f"Error in intent detection LLM call: {e}")
            return '{"function_call": {"name": "continue_chat"}}'

        # RecordLLMCall completion time
        llm_time = time.time() - llm_start_time
        logger.bind(tag=TAG).debug(
            f"External LLM intent recognition complete, model: {model_info}, call time: {llm_time:.4f}s"
        )

        # Record postprocessing start time
        postprocess_start_time = time.time()

        # Clean and parseResponse
        intent = intent.strip()
        # Try ExtractJSONPart
        match = re.search(r"\{.*\}", intent, re.DOTALL)
        if match:
            intent = match.group(0)

        # Record total processing time
        total_time = time.time() - total_start_time
        logger.bind(tag=TAG).debug(
            f"[Intent recognition performance] model: {model_info}, Total time: {total_time:.4f}seconds, LLMCall: {llm_time:.4f}seconds, Query: '{text[:20]}...'"
        )

        # Try to parse as JSON
        try:
            intent_data = json.loads(intent)
            # If function_call exists, format into processable format
            if "function_call" in intent_data:
                function_data = intent_data["function_call"]
                function_name = function_data.get("name")
                function_args = function_data.get("arguments", {})

                # Record recognizedfunction call
                logger.bind(tag=TAG).info(
                    f"llm Intent recognized: {function_name}, Parameter: {function_args}"
                )

                # Handle different intent types
                if function_name == "result_for_context":
                    # Handle basic info query, build result directly from context
                    logger.bind(tag=TAG).info(
                        "Detectedresult_for_contextIntent, will use contextInfoAnswer Directly"
                    )

                elif function_name == "continue_chat":
                    # Handle normal conversation
                    # Keep non-tool-relatedMessage
                    clean_history = [
                        msg
                        for msg in conn.dialogue.dialogue
                        if msg.role not in ["tool", "function"]
                    ]
                    conn.dialogue.dialogue = clean_history

                else:
                    # Handle function call
                    logger.bind(tag=TAG).info(f"Detected function call intent: {function_name}")

            # Unified cache handling and return
            self.cache_manager.set(self.CacheType.INTENT, cache_key, intent)
            postprocess_time = time.time() - postprocess_start_time
            logger.bind(tag=TAG).debug(f"Intent post-processing time: {postprocess_time:.4f}seconds")
            return intent
        except json.JSONDecodeError:
            # Post-processing time
            postprocess_time = time.time() - postprocess_start_time
            logger.bind(tag=TAG).error(
                f"Cannot parse intentJSON: {intent}, Post-processing cost: {postprocess_time:.4f}seconds"
            )
            # If parsing fails, default return continue chat intent
            return '{"function_call": {"name": "continue_chat"}}'
