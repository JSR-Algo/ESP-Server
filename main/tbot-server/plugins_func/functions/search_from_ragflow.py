import requests
import sys
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

# Define basic functionsDescriptionTemplate
SEARCH_FROM_RAGFLOW_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "search_from_ragflow",
        "description": "Query info from knowledge base",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "Query question"}},
            "required": ["question"],
        },
    },
}


@register_function(
    "search_from_ragflow", SEARCH_FROM_RAGFLOW_FUNCTION_DESC, ToolType.SYSTEM_CTL
)
def search_from_ragflow(conn: "ConnectionHandler", question=None):
    # Ensure string params handle encoding correctly
    if question and isinstance(question, str):
        # Ensure question param isUTF-8encoded string
        pass
    else:
        question = str(question) if question is not None else ""

    ragflow_config = conn.config.get("plugins", {}).get("search_from_ragflow", {})
    base_url = ragflow_config.get("base_url", "")
    api_key = ragflow_config.get("api_key", "")
    dataset_ids = ragflow_config.get("dataset_ids", [])

    url = base_url + "/api/v1/retrieval"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # EnsurepayloadStrings in are allUTF-8Encode
    payload = {"question": question, "dataset_ids": dataset_ids}

    try:
        # Useensure_ascii=FalseEnsureJSONHandle correctly during serializationChinese
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=5,
            verify=False,
        )

        # Explicitly SetResponseencoding isutf-8
        response.encoding = "utf-8"

        response.raise_for_status()

        # Get text firstContentThen handle manuallyJSONDecode
        response_text = response.text
        import json

        result = json.loads(response_text)

        if result.get("code") != 0:
            error_detail = result.get("error", {}).get("detail", "Unknown error")
            error_message = result.get("error", {}).get("message", "")
            error_code = result.get("code", "")

            # Safely recordError info
            logger.bind(tag=TAG).error(
                f"RAGFlow API call failed, response code: {error_code}, error details: {error_detail}, full response: {result}"
            )

            # Build detailedErrorResponse
            error_response = f"RAG API returned error (error code: {error_code})"

            if error_message:
                error_response += f":{error_message}"
            if error_detail:
                error_response += f"\nDetails: {error_detail}"

            return ActionResponse(Action.RESPONSE, None, error_response)

        chunks = result.get("data", {}).get("chunks", [])
        contents = []
        for chunk in chunks:
            content = chunk.get("content", "")
            if content:
                # Safely handleContentString
                if isinstance(content, str):
                    contents.append(content)
                elif isinstance(content, bytes):
                    contents.append(content.decode("utf-8", errors="replace"))
                else:
                    contents.append(str(content))

        if contents:
            # Organize knowledge baseContentas reference mode
            context_text = f"# Knowledge base found for question [{question}] as follows\n"
            context_text += "```\n\n\n".join(contents[:5])
            context_text += "\n```"
        else:
            context_text = "According to knowledge base query result, no relatedInfo."
        return ActionResponse(Action.REQLLM, context_text, None)

    except requests.exceptions.RequestException as e:
        # Network RequestException
        error_type = type(e).__name__
        logger.bind(tag=TAG).error(
            f"RAGflowNetwork request failed,ExceptionType:{error_type}, details:{str(e)}"
        )

        # Provide more detailed error info and solution based on exception type
        if isinstance(e, requests.exceptions.ConnectTimeout):
            error_response = "RAGAPI connection timeout (5seconds)"
            error_response += "\nPossible reason:RAGflowService not started or network connection issue"
            error_response += "\nSolution: please checkRAGflowServiceStatusand network connection"

        elif isinstance(e, requests.exceptions.ConnectionError):
            error_response = "Cannot connect toRAGInterface"
            error_response += "\nPossible reason:RAGflowService AddressErroror service not running"
            error_response += "\nSolution: please checkRAGflowService address config and serviceStatus"

        elif isinstance(e, requests.exceptions.Timeout):
            error_response = "RAGInterfaceRequest timeout"
            error_response += "\nPossible reason:RAGflowServiceResponseSlow or network latency"
            error_response += "\nSolution: please retry later or checkRAGflowService Performance"

        elif isinstance(e, requests.exceptions.HTTPError):
            # ProcessHTTPErrorStatuscode
            if hasattr(e.response, "status_code"):
                status_code = e.response.status_code
                error_response = f"RAGInterfaceHTTPError(Statuscode:{status_code})"

                # Try to get error info from response content
                try:
                    error_detail = e.response.json().get("error", {}).get("message", "")
                    if error_detail:
                        error_response += f"\nError details:{error_detail}"
                except:
                    pass
            else:
                error_response = f"RAGInterfaceHTTPException:{str(e)}"

        else:
            error_response = f"RAGAPI NetworkException({error_type}):{str(e)}"

        return ActionResponse(Action.RESPONSE, None, error_response)

    except Exception as e:
        # OtherException
        error_type = type(e).__name__
        logger.bind(tag=TAG).error(
            f"RAGflowProcessException,ExceptionType:{error_type}, details:{str(e)}"
        )

        # Provide detailed error info
        error_response = f"RAGAPI ProcessingException({error_type}):{str(e)}"
        return ActionResponse(Action.RESPONSE, None, error_response)
