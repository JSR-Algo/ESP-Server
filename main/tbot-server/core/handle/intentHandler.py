import json
import uuid
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.utils.dialogue import Message
from core.providers.tts.dto.dto import ContentType
from core.handle.helloHandle import checkWakeupWords
from plugins_func.register import Action, ActionResponse
from core.handle.sendAudioHandle import send_stt_message
from core.handle.reportHandle import enqueue_tool_report
from core.utils.util import remove_punctuation_and_length
from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType

TAG = __name__


async def handle_user_intent(conn: "ConnectionHandler", text):
    # Preprocess input text, handle possibleJSONFormat
    try:
        if text.strip().startswith("{") and text.strip().endswith("}"):
            parsed_data = json.loads(text)
            if isinstance(parsed_data, dict) and "content" in parsed_data:
                text = parsed_data["content"]  # ExtractcontentFor intent analysis
                conn.current_speaker = parsed_data.get("speaker")  # Keep speakerInfo
    except (json.JSONDecodeError, TypeError):
        pass

    # Check for explicit exit command
    _, filtered_text = remove_punctuation_and_length(text)
    if await check_direct_exit(conn, filtered_text):
        return True

    # Check whether wake word
    if await checkWakeupWords(conn, filtered_text):
        return True

    if conn.intent_type == "function_call":
        # Use Supportfunction callingchat method,No longer perform intent analysis
        return False
    # UseLLMPerform intent analysis
    intent_result = await analyze_intent_with_llm(conn, text)
    if not intent_result:
        return False
    # Generate at session startsentence_id
    conn.sentence_id = str(uuid.uuid4().hex)
    # Handle various intents
    return await process_intent_result(conn, intent_result, text)


async def check_direct_exit(conn: "ConnectionHandler", text):
    """Check for explicit exit command"""
    _, text = remove_punctuation_and_length(text)
    cmd_exit = conn.cmd_exit
    for cmd in cmd_exit:
        if text == cmd:
            conn.logger.bind(tag=TAG).info(f"Explicit exit command detected: {text}")
            await send_stt_message(conn, text)
            await conn.close()
            return True
    return False


async def analyze_intent_with_llm(conn: "ConnectionHandler", text):
    """Use LLM to analyze user intent"""
    if not hasattr(conn, "intent") or not conn.intent:
        conn.logger.bind(tag=TAG).warning("Intent recognition service not initialized")
        return None

    # Conversation history records
    dialogue = conn.dialogue
    try:
        intent_result = await conn.intent.detect_intent(conn, dialogue.dialogue, text)
        return intent_result
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"Intent recognition failed: {str(e)}")

    return None


async def process_intent_result(
    conn: "ConnectionHandler", intent_result, original_text
):
    """Handle intent recognition result"""
    try:
        # Try parse result asJSON
        intent_data = json.loads(intent_result)

        # Check whether hasfunction_call
        if "function_call" in intent_data:
            # Obtained directly from intent recognitionfunction_call
            conn.logger.bind(tag=TAG).debug(
                f"Detectedfunction_callIntent result in format: {intent_data['function_call']['name']}"
            )
            function_name = intent_data["function_call"]["name"]
            if function_name == "continue_chat":
                return False

            if function_name == "result_for_context":
                await send_stt_message(conn, original_text)
                conn.client_abort = False

                def process_context_result():
                    conn.dialogue.put(Message(role="user", content=original_text))

                    from core.utils.current_time import get_current_time_info

                    current_time, today_date, today_weekday, lunar_date = (
                        get_current_time_info()
                    )

                    # Build context-aware basePrompt
                    context_prompt = f"""Current time:{current_time}
                                        Today date:{today_date} ({today_weekday})
                                        Today lunar date:{lunar_date}

                                        Please based on aboveInfoAnswer user question:{original_text}"""

                    response = conn.intent.replyResult(context_prompt, original_text)
                    speak_txt(conn, response)

                conn.executor.submit(process_context_result)
                return True

            function_args = {}
            if "arguments" in intent_data["function_call"]:
                function_args = intent_data["function_call"]["arguments"]
                if function_args is None:
                    function_args = {}
            # Ensure parameter is string formatJSON
            if isinstance(function_args, dict):
                function_args = json.dumps(function_args)

            function_call_data = {
                "name": function_name,
                "id": str(uuid.uuid4().hex),
                "arguments": function_args,
            }

            await send_stt_message(conn, original_text)
            conn.client_abort = False

            # Prepare tool call params
            tool_input = {}
            if function_args:
                if isinstance(function_args, str):
                    tool_input = json.loads(function_args) if function_args else {}
                elif isinstance(function_args, dict):  # pragma: no cover - dict args are normalized to JSON above.
                    tool_input = function_args

            # Report tool call
            enqueue_tool_report(conn, function_name, tool_input)

            # UseexecutorExecute function call and result handling
            def process_function_call():
                conn.dialogue.put(Message(role="user", content=original_text))
                
                # Tool call timeout
                tool_call_timeout = int(conn.config.get("tool_call_timeout", 30))
                # UseUnified tool handlerProcess all tool calls
                try:
                    result = asyncio.run_coroutine_threadsafe(
                        conn.func_handler.handle_llm_function_call(
                            conn, function_call_data
                        ),
                        conn.loop,
                    ).result(timeout=tool_call_timeout)
                except Exception as e:
                    conn.logger.bind(tag=TAG).error(f"Tool call failed: {e}")
                    result = ActionResponse(
                        action=Action.ERROR, result="Tool call timed out, please try again later", response="Tool call timed out, please try again later"
                    )

                # Report tool call result
                if result:
                    enqueue_tool_report(conn, function_name, tool_input, str(result.result) if result.result else None, report_tool_call=False)

                    if result.action == Action.RESPONSE:  # Direct replyFrontend
                        text = result.response
                        if text is not None:
                            speak_txt(conn, text)
                    elif result.action == Action.REQLLM:  # Request llm to generate reply after calling function
                        text = result.result
                        conn.dialogue.put(Message(role="tool", content=text))
                        llm_result = conn.intent.replyResult(text, original_text)
                        if llm_result is None:
                            llm_result = text
                        speak_txt(conn, llm_result)
                    elif (
                        result.action == Action.NOTFOUND
                        or result.action == Action.ERROR
                    ):
                        text = result.response if result.response else result.result
                        if text is not None:
                            speak_txt(conn, text)
                    elif function_name != "play_music":
                        # For backward compatibility with original code
                        # Get current latest text index
                        text = result.response
                        if text is None:
                            text = result.result
                        if text is not None:
                            speak_txt(conn, text)

            # Put function execution in thread pool
            conn.executor.submit(process_function_call)
            return True
        return False
    except json.JSONDecodeError as e:
        conn.logger.bind(tag=TAG).error(f"Error handling intent result: {e}")
        return False


def speak_txt(conn: "ConnectionHandler", text):
    # Record textto sentence_id Map
    conn.tts.store_tts_text(conn.sentence_id, text)

    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.FIRST,
            content_type=ContentType.ACTION,
        )
    )
    conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=text)
    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.LAST,
            content_type=ContentType.ACTION,
        )
    )
    conn.dialogue.put(Message(role="assistant", content=text))
