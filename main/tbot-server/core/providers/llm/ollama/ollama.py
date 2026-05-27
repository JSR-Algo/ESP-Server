from config.logger import setup_logging
from openai import OpenAI
import json
from core.providers.llm.base import LLMProviderBase

TAG = __name__
logger = setup_logging()


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        self.model_name = config.get("model_name")
        self.base_url = config.get("base_url", "http://localhost:11434")
        # Initialize OpenAI client with Ollama base URL
        # If nonev1, addv1
        if not self.base_url.endswith("/v1"):
            self.base_url = f"{self.base_url}/v1"

        self.client = OpenAI(
            base_url=self.base_url,
            api_key="ollama",  # Ollama doesn't need an API key but OpenAI client requires one
        )

        # Check whether isqwen3Model
        self.is_qwen3 = self.model_name and self.model_name.lower().startswith("qwen3")

    def response(self, session_id, dialogue, **kwargs):
        # If isqwen3model, in user's lastMessageAdd in/no_thinkInstruction
        if self.is_qwen3:
            # Copy dialog list, avoidModifyOriginal Dialogue
            dialogue_copy = dialogue.copy()

            # Find last userMessage
            for i in range(len(dialogue_copy) - 1, -1, -1):
                if dialogue_copy[i]["role"] == "user":
                    # In userMessageAdd before/no_thinkInstruction
                    dialogue_copy[i]["content"] = (
                        "/no_think " + dialogue_copy[i]["content"]
                    )
                    logger.bind(tag=TAG).debug(f"Add /no_think instruction for qwen3 model")
                    break

            # UseModifyDialogue After
            dialogue = dialogue_copy

        responses = self.client.chat.completions.create(
            model=self.model_name, messages=dialogue, stream=True
        )
        is_active = True
        # Used handle crosschunkTag of
        buffer = ""

        try:
            for chunk in responses:
                try:
                    delta = (
                        chunk.choices[0].delta
                        if getattr(chunk, "choices", None)
                        else None
                    )
                    content = delta.content if hasattr(delta, "content") else ""

                    if content:
                        # willContentAdd to buffer
                        buffer += content

                        # Process tags in buffer
                        while "<think>" in buffer and "</think>" in buffer:
                            # Found complete<think></think>tag and remove
                            pre = buffer.split("<think>", 1)[0]
                            post = buffer.split("</think>", 1)[1]
                            buffer = pre + post

                        # Handle only start tag case
                        if "<think>" in buffer:
                            is_active = False
                            buffer = buffer.split("<think>", 1)[0]

                        # Handle only end tag case
                        if "</think>" in buffer:
                            is_active = True
                            buffer = buffer.split("</think>", 1)[1]

                        # If currently activeStatusand buffer hasContent, then Output
                        if is_active and buffer:
                            yield buffer
                            buffer = ""  # Clear buffer

                except Exception as e:
                    logger.bind(tag=TAG).error(f"Error processing chunk: {e}")
        finally:
            responses.close()

    def response_with_functions(self, session_id, dialogue, functions=None):
        # If isqwen3model, in user's lastMessageAdd in/no_thinkInstruction
        if self.is_qwen3:
            # Copy dialog list, avoidModifyOriginal Dialogue
            dialogue_copy = dialogue.copy()

            # Find last userMessage
            for i in range(len(dialogue_copy) - 1, -1, -1):
                if dialogue_copy[i]["role"] == "user":
                    # In userMessageAdd before/no_thinkInstruction
                    dialogue_copy[i]["content"] = (
                        "/no_think " + dialogue_copy[i]["content"]
                    )
                    logger.bind(tag=TAG).debug(f"Add /no_think instruction for qwen3 model")
                    break

            # UseModifyDialogue After
            dialogue = dialogue_copy

        stream = self.client.chat.completions.create(
            model=self.model_name,
            messages=dialogue,
            stream=True,
            tools=functions,
        )

        is_active = True
        buffer = ""

        try:
            for chunk in stream:
                try:
                    delta = (
                        chunk.choices[0].delta
                        if getattr(chunk, "choices", None)
                        else None
                    )
                    content = delta.content if hasattr(delta, "content") else None
                    tool_calls = (
                        delta.tool_calls if hasattr(delta, "tool_calls") else None
                    )

                    # If tool call, pass directly
                    if tool_calls:
                        yield None, tool_calls
                        continue

                    # Process TextContent
                    if content:
                        # willContentAdd to buffer
                        buffer += content

                        # Process tags in buffer
                        while "<think>" in buffer and "</think>" in buffer:
                            # Found complete<think></think>tag and remove
                            pre = buffer.split("<think>", 1)[0]
                            post = buffer.split("</think>", 1)[1]
                            buffer = pre + post

                        # Handle only start tag case
                        if "<think>" in buffer:
                            is_active = False
                            buffer = buffer.split("<think>", 1)[0]

                        # Handle only end tag case
                        if "</think>" in buffer:
                            is_active = True
                            buffer = buffer.split("</think>", 1)[1]

                        # If currently activeStatusand buffer hasContent, then Output
                        if is_active and buffer:
                            yield buffer, None
                            buffer = ""  # Clear buffer
                except Exception as e:
                    logger.bind(tag=TAG).error(f"Error processing function chunk: {e}")
                    continue
        finally:
            stream.close()
