# Gemini Vietnamese Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configure the ESP32 server agent to use Gemini as the LLM and answer in Vietnamese by default.

**Architecture:** Add an explicit root-level `response_language` setting and let `PromptManager` prefer it over TTS-derived language. Update server config so `selected_module.LLM` uses `GeminiLLM`, with optional Gemini TTS voice output retained in config.

**Tech Stack:** Python 3.10, Jinja2 prompt templates, YAML config, pytest-style Python tests.

---

## File Structure

- Modify: `esp32-server/main/tbot-server/config.yaml`
  - Selects `GeminiLLM`.
  - Adds `response_language: "Vietnamese"`.
  - Updates `GeminiTTS` model/style for Vietnamese voice output.
- Modify: `esp32-server/main/tbot-server/core/utils/prompt_manager.py`
  - Resolves prompt language from `response_language` first.
- Create: `esp32-server/main/tbot-server/tests/test_prompt_manager_language.py`
  - Verifies prompt rendering uses `response_language`.
  - Verifies fallback to selected TTS `language` still works.

---

### Task 1: Add Prompt Language Tests

**Files:**
- Create: `esp32-server/main/tbot-server/tests/test_prompt_manager_language.py`
- Modify: none

- [ ] **Step 1: Write the failing tests**

Create `esp32-server/main/tbot-server/tests/test_prompt_manager_language.py`:

```python
from core.utils.prompt_manager import PromptManager


class DummyCacheType:
    CONFIG = "config"
    DEVICE_PROMPT = "device_prompt"
    LOCATION = "location"
    WEATHER = "weather"


class DummyCacheManager:
    def __init__(self):
        self.values = {}

    def get(self, cache_type, key):
        return self.values.get((cache_type, key))

    def set(self, cache_type, key, value):
        self.values[(cache_type, key)] = value


class DummyLogger:
    def bind(self, **kwargs):
        return self

    def debug(self, message):
        pass

    def info(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


def make_prompt_manager(config):
    manager = PromptManager.__new__(PromptManager)
    manager.config = config
    manager.logger = DummyLogger()
    manager.base_prompt_template = "Reply language: {{language}}"
    manager.cache_manager = DummyCacheManager()
    manager.CacheType = DummyCacheType
    manager.context_data = ""
    return manager


def test_response_language_overrides_tts_language():
    manager = make_prompt_manager(
        {
            "response_language": "Vietnamese",
            "selected_module": {"TTS": "EdgeTTS"},
            "TTS": {"EdgeTTS": {"language": "Chinese"}},
        }
    )

    prompt = manager.build_enhanced_prompt("base prompt", "device-1")

    assert "Reply language: Vietnamese" in prompt


def test_tts_language_remains_fallback_when_response_language_missing():
    manager = make_prompt_manager(
        {
            "selected_module": {"TTS": "EdgeTTS"},
            "TTS": {"EdgeTTS": {"language": "Vietnamese"}},
        }
    )

    prompt = manager.build_enhanced_prompt("base prompt", "device-1")

    assert "Reply language: Vietnamese" in prompt
```

- [ ] **Step 2: Run tests to verify red**

Run:

```bash
cd esp32-server/main/tbot-server
PYTHONPATH=. pytest tests/test_prompt_manager_language.py -q
```

Expected: `test_response_language_overrides_tts_language` fails because the prompt still renders `Chinese`.

- [ ] **Step 3: Commit red tests**

```bash
git -C esp32-server add main/tbot-server/tests/test_prompt_manager_language.py
git -C esp32-server commit -m "test: cover prompt response language"
```

---

### Task 2: Implement Response Language Resolution

**Files:**
- Modify: `esp32-server/main/tbot-server/core/utils/prompt_manager.py`
- Test: `esp32-server/main/tbot-server/tests/test_prompt_manager_language.py`

- [ ] **Step 1: Update `PromptManager.build_enhanced_prompt()`**

In `esp32-server/main/tbot-server/core/utils/prompt_manager.py`, replace the current language block:

```python
            # GetTTSSelectedLanguage, default value isChinese
            language = (
                self.config.get("TTS", {})
                .get(self.config.get("selected_module", {}).get("TTS", ""), {})
                .get("language")
                or "Chinese"
            )
```

with:

```python
            # Prefer explicit response language, then fall back to selected TTS language.
            selected_tts = self.config.get("selected_module", {}).get("TTS", "")
            language = (
                self.config.get("response_language")
                or self.config.get("TTS", {}).get(selected_tts, {}).get("language")
                or "Chinese"
            )
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
cd esp32-server/main/tbot-server
PYTHONPATH=. pytest tests/test_prompt_manager_language.py -q
```

Expected: `2 passed`.

- [ ] **Step 3: Commit implementation**

```bash
git -C esp32-server add main/tbot-server/core/utils/prompt_manager.py
git -C esp32-server commit -m "feat: support explicit response language"
```

---

### Task 3: Configure Gemini LLM and Vietnamese Defaults

**Files:**
- Modify: `esp32-server/main/tbot-server/config.yaml`
- Test: none

- [ ] **Step 1: Update selected modules**

In `esp32-server/main/tbot-server/config.yaml`, change:

```yaml
  LLM: ChatGLMLLM
```

to:

```yaml
  LLM: GeminiLLM
```

If Gemini TTS voice output should be enabled now, change:

```yaml
  TTS: EdgeTTS
```

to:

```yaml
  TTS: GeminiTTS
```

- [ ] **Step 2: Add explicit response language**

Add this root-level setting near the existing `prompt:` section:

```yaml
response_language: "Vietnamese"
```

- [ ] **Step 3: Update Gemini TTS defaults**

In `TTS.GeminiTTS`, set:

```yaml
    model_name: "gemini-2.5-flash-preview-tts"
    voice: "Kore"
    style_instructions: "Nói tiếng Việt tự nhiên, rõ ràng, thân thiện."
```

Leave API key placeholders unchanged unless the deployer provides real secrets:

```yaml
    api_key: 你的gemini web key
```

- [ ] **Step 4: Validate YAML parses**

Run:

```bash
cd esp32-server/main/tbot-server
python - <<'PY'
from ruamel.yaml import YAML
with open("config.yaml", "r", encoding="utf-8") as f:
    data = YAML().load(f)
assert data["selected_module"]["LLM"] == "GeminiLLM"
assert data["response_language"] == "Vietnamese"
print("config ok")
PY
```

Expected: `config ok`.

- [ ] **Step 5: Commit config**

```bash
git -C esp32-server add main/tbot-server/config.yaml
git -C esp32-server commit -m "chore: configure gemini vietnamese agent"
```

---

### Task 4: Final Verification

**Files:**
- Modify: none
- Test: `esp32-server/main/tbot-server/tests/test_prompt_manager_language.py`

- [ ] **Step 1: Run focused tests**

```bash
cd esp32-server/main/tbot-server
PYTHONPATH=. pytest tests/test_prompt_manager_language.py -q
```

Expected: `2 passed`.

- [ ] **Step 2: Run config validation**

```bash
cd esp32-server/main/tbot-server
python - <<'PY'
from ruamel.yaml import YAML
with open("config.yaml", "r", encoding="utf-8") as f:
    data = YAML().load(f)
assert data["selected_module"]["LLM"] == "GeminiLLM"
assert data["response_language"] == "Vietnamese"
print("config ok")
PY
```

Expected: `config ok`.

- [ ] **Step 3: Record live-test limitation**

Live Gemini response and TTS verification require:

```text
Gemini API key
Network access to generativelanguage.googleapis.com
Server runtime with device connection
```

Do not claim live Gemini works unless those prerequisites are available and tested.

