from config.logger import setup_logging
from http import HTTPStatus
import dashscope
from dashscope import Application
from core.providers.llm.base import LLMProviderBase
from core.utils.util import check_model_key
import time

TAG = __name__
logger = setup_logging()


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        self.api_key = config["api_key"]
        self.app_id = config["app_id"]
        self.base_url = config.get("base_url")
        self.is_No_prompt = config.get("is_no_prompt")
        self.memory_id = config.get("ali_memory_id")
        self.streaming_chunk_size = config.get("streaming_chunk_size", 3)  # Characters per streaming return
        check_model_key("AliBLLLM", self.api_key)

    def response(self, session_id, dialogue):
        # Processdialogue
        if self.is_No_prompt:
            dialogue.pop(0)
            logger.bind(tag=TAG).debug(
                f"[Alibaba Bailian API Service] Processed dialogue: {dialogue}"
            )

        # Construct call parameters
        call_params = {
            "api_key": self.api_key,
            "app_id": self.app_id,
            "session_id": session_id,
            "messages": dialogue,
            # EnableSDKNative Streaming
            "stream": True,
        }
        if self.memory_id != False:
            # BailianmemoryNeedpromptParameter
            prompt = dialogue[-1].get("content")
            call_params["memory_id"] = self.memory_id
            call_params["prompt"] = prompt
            logger.bind(tag=TAG).debug(
                f"[Alibaba Bailian API Service]processed prompt: {prompt}"
            )

        # Optionally set customAPIBase address (if configured as compatible modeURLthen Ignore)
        if self.base_url and ("/api/" in self.base_url):
            dashscope.base_http_api_url = self.base_url

        responses = Application.call(**call_params)

        # Streaming processing (SDKinstream=TrueReturn iterable object when; otherwise return singleResponseObject)
        logger.bind(tag=TAG).debug(
            f"[Alibaba BailianAPIService] constructor params: {dict(call_params, api_key='***')}"
        )

        last_text = ""
        try:
            for resp in responses:
                if resp.status_code != HTTPStatus.OK:
                    logger.bind(tag=TAG).error(
                        f"code={resp.status_code}, message={resp.message}, Refer toDocument:https://help.aliyun.com/zh/model-studio/developer-reference/error-code"
                    )
                    continue
                current_text = getattr(getattr(resp, "output", None), "text", None)
                if current_text is None:
                    continue
                # SDKStreaming is incremental overwrite, calculate delta output
                if len(current_text) >= len(last_text):
                    delta = current_text[len(last_text):]
                else:
                    # Avoid occasional fallback
                    delta = current_text
                if delta:
                    yield delta
                last_text = current_text
        except TypeError:
            # Non-stream fallback (one-time return)
            if responses.status_code != HTTPStatus.OK:
                logger.bind(tag=TAG).error(
                    f"code={responses.status_code}, message={responses.message}, Refer toDocument:https://help.aliyun.com/zh/model-studio/developer-reference/error-code"
                )
                yield "[Alibaba BailianAPIServiceResponseException]"
            else:
                full_text = getattr(getattr(responses, "output", None), "text", "")
                logger.bind(tag=TAG).info(
                    f"[Alibaba BailianAPIservice] completeResponseLength: {len(full_text)}"
                )
                for i in range(0, len(full_text), self.streaming_chunk_size):
                    chunk = full_text[i:i + self.streaming_chunk_size]
                    if chunk:
                        yield chunk

    def response_with_functions(self, session_id, dialogue, functions=None):
        # Alibaba Bailian currently does not support native function call. To maintain compatibility, fall back to normal text streaming output here.
        # Upper Layer Will (content, tool_calls) Consume in form of, here always return (token, None)
        logger.bind(tag=TAG).warning(
            "Alibaba Bailian no native implementation function call, fallen back to plain text streaming output"
        )
        for token in self.response(session_id, dialogue):
            yield token, None
