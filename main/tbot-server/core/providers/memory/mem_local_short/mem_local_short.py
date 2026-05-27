from ..base import MemoryProviderBase, logger
import time
import json
import os
import yaml
from config.config_loader import get_project_dir
from config.manage_api_client import generate_and_save_chat_summary
import asyncio
from core.utils.util import check_model_key


short_term_memory_prompt = """
# Spacetime Memory Weaver

## Core Mission
Build growable dynamic memory network, keep key information in limited space, and intelligently maintain information evolution path
Summarize important user information from conversation records to provide more personalized service in future conversations

## Memory Rules
### 1. Three-dimensional Memory Evaluation (must run on every update)
| Dimension       | Evaluation Criteria                  | Weight |
|------------|---------------------------|--------|
| Timeliness     | Information freshness (by conversation turn) | 40%    |
| Emotional intensity   | Contains 💖 marker / repeated mention count     | 35%    |
| Association density   | Number of connections with other information      | 25%    |

### 2. Dynamic Update Mechanism
**Name change handling example:**
Original memory:"Former name": ["Zhang San"], "Current name": "Zhang Sanfeng"
Trigger condition: when naming signals like "my name is X" or "call me Y" are detected
Operation flow:
1. Move old name to"Former name"List
2. Record naming timeline:"2024-02-15 14:32:EnableZhang Sanfeng"
3. Append to memory cube: "Identity transformation from Zhang San to Zhang Sanfeng"

### 3. Space optimization strategy
- **Information compression technique**: Use symbol system to improve density
  - ✅"Zhang Sanfeng[north/Software engineering/🐱]"
  - ❌"Beijing software engineer, keeps cat"
- **Elimination Warning**: when total words≥900Trigger when
  1. DeleteWeight score<60and3rounds not mentionedInfo
  2. Merge similar entries (keepTimestamprecent)

## Memory Structure
Output format must be parseablejsonString, no explanation, comments, or notes needed,SaveExtract only from dialog when rememberingInfoDo not mix examplesContent
```json
{
  "Spacetime archive": {
    "Identity graph": {
      "Current name": "",
      "Feature tag": [] 
    },
    "Memory cube": [
      {
        "Event": "Join new company",
        "Timestamp": "2024-03-20",
        "Emotion value": 0.9,
        "Related item": ["Afternoon tea"],
        "Shelf life": 30 
      }
    ]
  },
  "Relationship network": {
    "High-frequency topics": {"Workplace": 12},
    "Hidden connections": [""]
  },
  "Pending response": {
    "Urgent matters": ["Tasks needing immediate handling"], 
    "Potential care": ["Help proactively offered"]
  },
  "Highlight quotes": [
    "Most moving moment, strong emotional expression, user's original words"
  ]
}
```
"""


def extract_json_data(json_code):
    start = json_code.find("```json")
    # fromstartStart find next```End
    end = json_code.find("```", start + 1)
    # print("start:", start, "end:", end)
    if start == -1 or end == -1:
        try:
            jsonData = json.loads(json_code)
            return json_code
        except Exception as e:
            print("Error:", e)
        return ""
    jsonData = json_code[start + 7 : end]
    return jsonData


TAG = __name__


class MemoryProvider(MemoryProviderBase):
    def __init__(self, config, summary_memory):
        super().__init__(config)
        self.short_memory = ""
        self.save_to_file = True
        self.memory_path = get_project_dir() + "data/.memory.yaml"
        self.load_memory(summary_memory)

    def init_memory(
        self, role_id, llm, summary_memory=None, save_to_file=True, **kwargs
    ):
        super().init_memory(role_id, llm, **kwargs)
        self.save_to_file = save_to_file
        self.load_memory(summary_memory)

    def load_memory(self, summary_memory):
        # apiGotSummary memoryreturn directly after
        if summary_memory or not self.save_to_file:
            self.short_memory = summary_memory
            return

        all_memory = {}
        if os.path.exists(self.memory_path):
            with open(self.memory_path, "r", encoding="utf-8") as f:
                all_memory = yaml.safe_load(f) or {}
        if self.role_id in all_memory:
            self.short_memory = all_memory[self.role_id]

    def save_memory_to_file(self):
        all_memory = {}
        if os.path.exists(self.memory_path):
            with open(self.memory_path, "r", encoding="utf-8") as f:
                all_memory = yaml.safe_load(f) or {}
        all_memory[self.role_id] = self.short_memory
        with open(self.memory_path, "w", encoding="utf-8") as f:
            yaml.dump(all_memory, f, allow_unicode=True)

    async def save_memory(self, msgs, session_id=None):
        llm = getattr(self, "llm", None)
        if llm is None:
            logger.bind(tag=TAG).error("LLM is not set for memory provider")
            return None
        # Print used modelInfo
        model_info = getattr(llm, "model_name", str(llm.__class__.__name__))
        logger.bind(tag=TAG).debug(f"Use MemorySaveModel: {model_info}")
        api_key = getattr(llm, "api_key", None)
        memory_key_msg = check_model_key("For memory summaryLLM", api_key)
        if memory_key_msg:
            logger.bind(tag=TAG).error(memory_key_msg)

        if len(msgs) < 2:
            return None

        msgStr = ""
        for msg in msgs:
            content = msg.content

            # Extract content from JSON format if present (for ASR with emotion/language tags)
            try:
                if content and content.strip().startswith("{") and content.strip().endswith("}"):
                    data = json.loads(content)
                    if "content" in data:
                        content = data["content"]
            except (json.JSONDecodeError, KeyError, TypeError):
                # If parsing fails, use original content
                pass

            if msg.role == "user":
                msgStr += f"User: {content}\n"
            elif msg.role == "assistant":
                msgStr += f"Assistant: {content}\n"
        if self.short_memory and len(self.short_memory) > 0:
            msgStr += "Historical memory:\n"
            msgStr += self.short_memory

        # Current Time
        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        msgStr += f"Current time:{time_str}"

        if self.save_to_file:
            try:
                result = self.llm.response_no_stream(
                    short_term_memory_prompt,
                    msgStr,
                    max_tokens=2000,
                    temperature=0.2,
                )
                json_str = extract_json_data(result)
                json.loads(json_str)  # CheckjsonFormat correct or not
                self.short_memory = json_str
                self.save_memory_to_file()
            except Exception as e:
                logger.bind(tag=TAG).error(f"Error in saving memory: {e}")
        else:
            # whensave_to_fileforFalsewhen, callJavaserver-side chat history summary API
            summary_id = session_id if session_id else self.role_id
            await generate_and_save_chat_summary(summary_id)
        logger.bind(tag=TAG).info(
            f"Save memory successful - Role: {self.role_id}, Session: {session_id}"
        )

        return self.short_memory

    async def query_memory(self, query: str) -> str:
        return self.short_memory
