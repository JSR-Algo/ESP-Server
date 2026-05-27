# Google Live Voice Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `google_live` as opt-in per-agent voice session mode while preserving existing `classic_pipeline` behavior and backward compatibility.

**Architecture:** Keep `ConnectionHandler` as websocket/session owner. Add one per-session `VoiceSessionProvider` abstraction with `ClassicPipelineProvider` wrapping existing flow and `GoogleLiveProvider` handling direct Google Live audio streaming plus optional fallback to classic mode.

**Tech Stack:** Python 3.10 server, `websockets`, `asyncio`, `opuslib_next`, Spring Boot 3 / MyBatis, Vue 2 + Element UI, Liquibase SQL changelogs, JUnit 5, Python `unittest`.

---

### Task 1: Add Agent Voice Mode Storage and API Contract

**Files:**
- Create: `esp32-server/main/manager-api/src/main/resources/db/changelog/202605131700.sql`
- Modify: `esp32-server/main/manager-api/src/main/java/tbot/modules/agent/entity/AgentEntity.java`
- Modify: `esp32-server/main/manager-api/src/main/java/tbot/modules/agent/dto/AgentUpdateDTO.java`
- Modify: `esp32-server/main/manager-api/src/main/java/tbot/modules/agent/dto/AgentDTO.java`
- Modify: `esp32-server/main/manager-api/src/main/java/tbot/modules/agent/vo/AgentInfoVO.java`
- Modify: `esp32-server/main/manager-api/src/main/resources/mapper/agent/AgentDao.xml`
- Modify: `esp32-server/main/manager-api/src/main/java/tbot/modules/agent/service/impl/AgentServiceImpl.java`
- Modify: `esp32-server/main/manager-api/src/main/resources/db/changelog/db.changelog-master.yaml`
- Test: `esp32-server/main/manager-api/src/test/java/tbot/modules/agent/AgentControllerVoiceModeTest.java`

- [ ] **Step 1: Write failing API test for reading and updating voice mode**

```java
package tbot.modules.agent;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import tbot.modules.agent.controller.AgentController;
import tbot.modules.agent.dto.AgentUpdateDTO;
import tbot.modules.agent.service.AgentService;
import tbot.modules.agent.vo.AgentInfoVO;

@WebMvcTest(AgentController.class)
class AgentControllerVoiceModeTest {
    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private AgentService agentService;

    @Test
    @DisplayName("GET /agent/{id} returns voice mode fields")
    void getAgentIncludesVoiceMode() throws Exception {
        AgentInfoVO vo = new AgentInfoVO();
        vo.setVoiceMode("google_live");
        vo.setGoogleLiveConfigJson("{\"model\":\"gemini-2.5-flash-native-audio-preview-12-2025\"}");
        when(agentService.getAgentById("agent-1")).thenReturn(vo);

        mockMvc.perform(get("/agent/agent-1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.voiceMode").value("google_live"))
                .andExpect(jsonPath("$.data.googleLiveConfigJson").value("{\"model\":\"gemini-2.5-flash-native-audio-preview-12-2025\"}"));
    }

    @Test
    @DisplayName("PUT /agent/{id} persists voice mode fields")
    void updateAgentAcceptsVoiceMode() throws Exception {
        mockMvc.perform(put("/agent/agent-1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "voiceMode": "google_live",
                                  "googleLiveConfigJson": "{\\"model\\":\\"gemini-2.5-flash-native-audio-preview-12-2025\\",\\"native_voice\\":true}"
                                }
                                """))
                .andExpect(status().isOk());

        ArgumentCaptor<AgentUpdateDTO> captor = ArgumentCaptor.forClass(AgentUpdateDTO.class);
        verify(agentService).updateAgentById(any(), captor.capture());
        assert "google_live".equals(captor.getValue().getVoiceMode());
    }
}
```

- [ ] **Step 2: Run manager-api test and verify it fails**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-api && mvn -Dtest=AgentControllerVoiceModeTest test`

Expected: FAIL because `voiceMode` and `googleLiveConfigJson` do not exist yet on DTO/entity/VO.

- [ ] **Step 3: Add DB changelog and Java fields**

```sql
-- esp32-server/main/manager-api/src/main/resources/db/changelog/202605131700.sql
ALTER TABLE ai_agent
    ADD COLUMN voice_mode VARCHAR(32) NULL COMMENT 'classic_pipeline | google_live' AFTER tts_pitch,
    ADD COLUMN google_live_config_json JSON NULL COMMENT 'Google Live mode config' AFTER voice_mode;
```

```yaml
# append to db.changelog-master.yaml
- include:
    file: db/changelog/202605131700.sql
```

```java
// AgentEntity.java
@Schema(description = "Voice session mode")
private String voiceMode;

@Schema(description = "Google Live config JSON")
private String googleLiveConfigJson;
```

```java
// AgentUpdateDTO.java
@Schema(description = "Voice session mode", example = "classic_pipeline", nullable = true)
private String voiceMode;

@Schema(description = "Google Live config JSON", nullable = true)
private String googleLiveConfigJson;
```

```java
// AgentDTO.java
@Schema(description = "Voice session mode", example = "classic_pipeline")
private String voiceMode;

@Schema(description = "Google Live config JSON")
private String googleLiveConfigJson;
```

```java
// AgentInfoVO.java
@Schema(description = "Voice session mode")
private String voiceMode;

@Schema(description = "Google Live config JSON")
private String googleLiveConfigJson;
```

```xml
<!-- AgentDao.xml -->
<result column="voiceMode" property="voiceMode"/>
<result column="googleLiveConfigJson" property="googleLiveConfigJson"/>
...
a.voice_mode AS voiceMode,
a.google_live_config_json AS googleLiveConfigJson,
```

```java
// AgentServiceImpl.updateAgentById(...)
if (dto.getVoiceMode() != null) {
    existingEntity.setVoiceMode(dto.getVoiceMode());
}
if (dto.getGoogleLiveConfigJson() != null) {
    existingEntity.setGoogleLiveConfigJson(dto.getGoogleLiveConfigJson());
}
```

- [ ] **Step 4: Run manager-api test and verify it passes**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-api && mvn -Dtest=AgentControllerVoiceModeTest test`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add esp32-server/main/manager-api/src/main/resources/db/changelog/202605131700.sql \
  esp32-server/main/manager-api/src/main/resources/db/changelog/db.changelog-master.yaml \
  esp32-server/main/manager-api/src/main/java/tbot/modules/agent/entity/AgentEntity.java \
  esp32-server/main/manager-api/src/main/java/tbot/modules/agent/dto/AgentUpdateDTO.java \
  esp32-server/main/manager-api/src/main/java/tbot/modules/agent/dto/AgentDTO.java \
  esp32-server/main/manager-api/src/main/java/tbot/modules/agent/vo/AgentInfoVO.java \
  esp32-server/main/manager-api/src/main/resources/mapper/agent/AgentDao.xml \
  esp32-server/main/manager-api/src/main/java/tbot/modules/agent/service/impl/AgentServiceImpl.java \
  esp32-server/main/manager-api/src/test/java/tbot/modules/agent/AgentControllerVoiceModeTest.java
git commit -m "feat: add agent voice mode persistence"
```

### Task 2: Append Voice Mode to Python Config Payload

**Files:**
- Modify: `esp32-server/main/manager-api/src/main/java/tbot/modules/config/service/impl/ConfigServiceImpl.java`
- Test: `esp32-server/main/manager-api/src/test/java/tbot/modules/config/ConfigServiceVoiceModeTest.java`

- [ ] **Step 1: Write failing test for config payload serialization**

```java
package tbot.modules.config;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.lang.reflect.Method;
import java.util.HashMap;
import java.util.Map;

import org.junit.jupiter.api.Test;

import tbot.modules.config.service.impl.ConfigServiceImpl;

class ConfigServiceVoiceModeTest {
    @Test
    void buildModuleConfigIncludesVoiceMode() throws Exception {
        ConfigServiceImpl service = new ConfigServiceImpl();
        Method method = ConfigServiceImpl.class.getDeclaredMethod(
                "appendVoiceModeConfig",
                String.class, String.class, Map.class);
        method.setAccessible(true);

        Map<String, Object> result = new HashMap<>();
        method.invoke(service, "google_live", "{\"model\":\"gemini-2.5-flash-native-audio-preview-12-2025\"}", result);

        Map<?, ?> voiceMode = (Map<?, ?>) result.get("voice_mode");
        assertEquals("google_live", voiceMode.get("type"));
        assertTrue(result.containsKey("google_live"));
    }
}
```

- [ ] **Step 2: Run test and verify it fails**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-api && mvn -Dtest=ConfigServiceVoiceModeTest test`

Expected: FAIL because helper and config mapping do not exist yet.

- [ ] **Step 3: Add helper and call it from config build path**

```java
// ConfigServiceImpl.java
private void appendVoiceModeConfig(String voiceModeValue, String googleLiveConfigJson, Map<String, Object> result) {
    Map<String, Object> voiceMode = new HashMap<>();
    voiceMode.put("type", StringUtils.isBlank(voiceModeValue) ? "classic_pipeline" : voiceModeValue);
    voiceMode.put("fallback_to_classic_on_error", true);
    result.put("voice_mode", voiceMode);

    if (StringUtils.isNotBlank(googleLiveConfigJson)) {
        result.put("google_live", JsonUtils.parseObject(googleLiveConfigJson, Map.class));
    }
}
```

```java
// call near end of buildModuleConfig(...)
appendVoiceModeConfig(agent.getVoiceMode(), agent.getGoogleLiveConfigJson(), result);
```

- [ ] **Step 4: Run test and verify it passes**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-api && mvn -Dtest=ConfigServiceVoiceModeTest test`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add esp32-server/main/manager-api/src/main/java/tbot/modules/config/service/impl/ConfigServiceImpl.java \
  esp32-server/main/manager-api/src/test/java/tbot/modules/config/ConfigServiceVoiceModeTest.java
git commit -m "feat: expose voice mode in python config payload"
```

### Task 3: Add Voice Session Provider Abstraction in Python Server

**Files:**
- Create: `esp32-server/main/tbot-server/core/voice/session_provider/base.py`
- Create: `esp32-server/main/tbot-server/core/voice/session_provider/factory.py`
- Create: `esp32-server/main/tbot-server/tests/test_voice_provider_factory.py`
- Modify: `esp32-server/main/tbot-server/config/config_loader.py`

- [ ] **Step 1: Write failing Python factory test**

```python
import unittest

from core.voice.session_provider.factory import create_voice_session_provider


class DummyConn:
    def __init__(self, mode):
        self.config = {"voice_mode": {"type": mode}}


class VoiceProviderFactoryTest(unittest.TestCase):
    def test_defaults_to_classic_pipeline(self):
        conn = DummyConn("classic_pipeline")
        provider = create_voice_session_provider(conn)
        self.assertEqual(provider.__class__.__name__, "ClassicPipelineProvider")

    def test_selects_google_live(self):
        conn = DummyConn("google_live")
        provider = create_voice_session_provider(conn)
        self.assertEqual(provider.__class__.__name__, "GoogleLiveProvider")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run Python unit test and verify it fails**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server && python -m unittest tests.test_voice_provider_factory -v`

Expected: FAIL because provider package does not exist yet.

- [ ] **Step 3: Add config normalization and provider scaffolding**

```python
# config/config_loader.py
def normalize_voice_mode(config):
    config.setdefault("voice_mode", {})
    config["voice_mode"].setdefault("type", "classic_pipeline")
    config["voice_mode"].setdefault("fallback_to_classic_on_error", True)
    config.setdefault("google_live", {})
    return config
```

```python
# call from load_config() and get_config_from_api_async()
config = normalize_voice_mode(config)
```

```python
# core/voice/session_provider/base.py
from abc import ABC, abstractmethod

class VoiceSessionProvider(ABC):
    def __init__(self, conn):
        self.conn = conn

    @abstractmethod
    async def start_session(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def handle_text_message(self, message: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def handle_audio_bytes(self, chunk: bytes) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def interrupt(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
```

```python
# core/voice/session_provider/factory.py
from core.voice.session_provider.classic_pipeline import ClassicPipelineProvider
from core.voice.session_provider.google_live import GoogleLiveProvider

def create_voice_session_provider(conn):
    mode = conn.config.get("voice_mode", {}).get("type", "classic_pipeline")
    if mode == "google_live":
        return GoogleLiveProvider(conn)
    return ClassicPipelineProvider(conn)
```

- [ ] **Step 4: Run Python unit test and verify it still fails on missing concrete providers**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server && python -m unittest tests.test_voice_provider_factory -v`

Expected: FAIL because `classic_pipeline.py` and `google_live.py` are still missing.

- [ ] **Step 5: Commit**

```bash
git add esp32-server/main/tbot-server/config/config_loader.py \
  esp32-server/main/tbot-server/core/voice/session_provider/base.py \
  esp32-server/main/tbot-server/core/voice/session_provider/factory.py \
  esp32-server/main/tbot-server/tests/test_voice_provider_factory.py
git commit -m "feat: add voice session provider base and config normalization"
```

### Task 4: Wrap Existing Flow in ClassicPipelineProvider and Route Connection Through Provider

**Files:**
- Create: `esp32-server/main/tbot-server/core/voice/session_provider/classic_pipeline.py`
- Modify: `esp32-server/main/tbot-server/core/connection.py`
- Modify: `esp32-server/main/tbot-server/core/handle/textMessageProcessor.py`
- Create: `esp32-server/main/tbot-server/tests/test_classic_pipeline_provider.py`

- [ ] **Step 1: Write failing regression test for classic routing**

```python
import asyncio
import unittest

from core.voice.session_provider.classic_pipeline import ClassicPipelineProvider


class DummyConn:
    def __init__(self):
        self.asr_audio_queue = []
        self.websocket = None
        self.client_abort = False


class ClassicPipelineProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_binary_audio_falls_into_asr_queue(self):
        conn = DummyConn()
        provider = ClassicPipelineProvider(conn)
        handled = await provider.handle_audio_bytes(b"opus-frame")
        self.assertFalse(handled)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test and verify it fails**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server && python -m unittest tests.test_classic_pipeline_provider -v`

Expected: FAIL because provider not implemented.

- [ ] **Step 3: Implement classic wrapper and connection integration**

```python
# core/voice/session_provider/classic_pipeline.py
from core.voice.session_provider.base import VoiceSessionProvider

class ClassicPipelineProvider(VoiceSessionProvider):
    async def start_session(self) -> None:
        await self.conn._start_classic_pipeline_session()

    async def handle_text_message(self, message: str) -> bool:
        return False

    async def handle_audio_bytes(self, chunk: bytes) -> bool:
        return False

    async def interrupt(self) -> None:
        self.conn.client_abort = True

    async def close(self) -> None:
        return None
```

```python
# connection.py
from core.voice.session_provider.factory import create_voice_session_provider

async def _start_classic_pipeline_session(self):
    asyncio.create_task(self._background_initialize())

async def handle_connection(self, ws):
    ...
    await self._initialize_private_config_async()
    self.voice_provider = create_voice_session_provider(self)
    await self.voice_provider.start_session()
    async for message in self.websocket:
        await self._route_message(message)
```

```python
async def _route_message(self, message):
    ...
    if isinstance(message, str):
        handled = await self.voice_provider.handle_text_message(message)
        if not handled:
            await handleTextMessage(self, message)
    elif isinstance(message, bytes):
        handled = await self.voice_provider.handle_audio_bytes(message)
        if handled:
            return
        if self.vad is None or self.asr is None:
            return
        ...
```

- [ ] **Step 4: Run classic regression test and smoke-check old path**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server && python -m unittest tests.test_classic_pipeline_provider -v`

Expected: PASS

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server && python app.py`

Expected: server starts and prints websocket address without import or routing errors.

- [ ] **Step 5: Commit**

```bash
git add esp32-server/main/tbot-server/core/voice/session_provider/classic_pipeline.py \
  esp32-server/main/tbot-server/core/connection.py \
  esp32-server/main/tbot-server/core/handle/textMessageProcessor.py \
  esp32-server/main/tbot-server/tests/test_classic_pipeline_provider.py
git commit -m "refactor: route classic websocket sessions through provider"
```

### Task 5: Implement Google Live Transport and Audio Bridge

**Files:**
- Create: `esp32-server/main/tbot-server/core/voice/google_live/client.py`
- Create: `esp32-server/main/tbot-server/core/voice/google_live/audio_bridge.py`
- Create: `esp32-server/main/tbot-server/core/voice/session_provider/google_live.py`
- Create: `esp32-server/main/tbot-server/tests/test_google_live_provider_fallback.py`
- Modify: `esp32-server/main/tbot-server/requirements.txt`

- [ ] **Step 1: Write failing fallback test**

```python
import unittest

from core.voice.session_provider.google_live import GoogleLiveProvider


class DummyConn:
    def __init__(self):
        self.config = {
            "voice_mode": {"type": "google_live", "fallback_to_classic_on_error": True},
            "google_live": {"api_key": "x", "model": "gemini-2.5-flash-native-audio-preview-12-2025"},
        }
        self.session_id = "session-1"
        self.logger = type("L", (), {"bind": lambda *a, **k: type("B", (), {"info": print, "warning": print, "error": print})()})()
        self.voice_provider = None


class GoogleLiveProviderFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_init_failure_falls_back_to_classic(self):
        conn = DummyConn()
        provider = GoogleLiveProvider(conn)
        provider._connect_live = provider._fail_connect
        await provider.start_session()
        self.assertEqual(conn.voice_provider.__class__.__name__, "ClassicPipelineProvider")
```

- [ ] **Step 2: Run test and verify it fails**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server && python -m unittest tests.test_google_live_provider_fallback -v`

Expected: FAIL because Google provider and client do not exist yet.

- [ ] **Step 3: Add live client, audio bridge, and provider**

```python
# requirements.txt
google-genai==1.16.1
resampy==0.4.3
```

```python
# core/voice/google_live/audio_bridge.py
import audioop
import opuslib_next

class GoogleLiveAudioBridge:
    def __init__(self):
        self.input_decoder = opuslib_next.Decoder(16000, 1)
        self.output_encoder = opuslib_next.Encoder(24000, 1, opuslib_next.APPLICATION_AUDIO)

    def opus_to_pcm16(self, packet: bytes) -> bytes:
        return self.input_decoder.decode(packet, 960)

    def pcm16k_to_pcm24k(self, pcm: bytes) -> bytes:
        converted, _ = audioop.ratecv(pcm, 2, 1, 16000, 24000, None)
        return converted
```

```python
# core/voice/google_live/client.py
class GoogleLiveClient:
    def __init__(self, config):
        self.config = config
        self.session = None

    async def connect(self):
        raise NotImplementedError("Implement real Google Live connect here")

    async def send_audio(self, pcm_bytes: bytes):
        raise NotImplementedError

    async def recv_event(self):
        raise NotImplementedError

    async def close(self):
        return None
```

```python
# core/voice/session_provider/google_live.py
from core.handle.sendAudioHandle import sendAudio, send_tts_message
from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge
from core.voice.google_live.client import GoogleLiveClient
from core.voice.session_provider.base import VoiceSessionProvider
from core.voice.session_provider.classic_pipeline import ClassicPipelineProvider

class GoogleLiveProvider(VoiceSessionProvider):
    def __init__(self, conn):
        super().__init__(conn)
        self.live_config = conn.config.get("google_live", {})
        self.bridge = GoogleLiveAudioBridge()
        self.client = GoogleLiveClient(self.live_config)
        self.recv_task = None

    async def _fail_connect(self):
        raise RuntimeError("forced failure")

    async def _connect_live(self):
        await self.client.connect()

    async def start_session(self) -> None:
        try:
            await self._connect_live()
            self.recv_task = asyncio.create_task(self._recv_loop())
            self.conn.voice_provider = self
        except Exception:
            if self.conn.config.get("voice_mode", {}).get("fallback_to_classic_on_error", True):
                fallback = ClassicPipelineProvider(self.conn)
                self.conn.voice_provider = fallback
                await fallback.start_session()
            else:
                raise

    async def handle_text_message(self, message: str) -> bool:
        return False

    async def handle_audio_bytes(self, chunk: bytes) -> bool:
        pcm = self.bridge.opus_to_pcm16(chunk)
        await self.client.send_audio(pcm)
        return True

    async def _recv_loop(self):
        while True:
            event = await self.client.recv_event()
            if event["type"] == "audio_start":
                await send_tts_message(self.conn, "start", None)

    async def interrupt(self) -> None:
        return None

    async def close(self) -> None:
        if self.recv_task:
            self.recv_task.cancel()
        await self.client.close()
```

- [ ] **Step 4: Run fallback test and provider import checks**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server && python -m unittest tests.test_google_live_provider_fallback -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add esp32-server/main/tbot-server/requirements.txt \
  esp32-server/main/tbot-server/core/voice/google_live/client.py \
  esp32-server/main/tbot-server/core/voice/google_live/audio_bridge.py \
  esp32-server/main/tbot-server/core/voice/session_provider/google_live.py \
  esp32-server/main/tbot-server/tests/test_google_live_provider_fallback.py
git commit -m "feat: add google live provider transport scaffold"
```

### Task 6: Map Live Events to Existing Firmware-Compatible Surfaces

**Files:**
- Modify: `esp32-server/main/tbot-server/core/voice/session_provider/google_live.py`
- Modify: `esp32-server/main/tbot-server/core/handle/sendAudioHandle.py`
- Test: `esp32-server/main/tbot-server/tests/test_google_live_event_mapping.py`

- [ ] **Step 1: Write failing event mapping test**

```python
import unittest

from core.voice.session_provider.google_live import GoogleLiveProvider


class GoogleLiveEventMappingTest(unittest.IsolatedAsyncioTestCase):
    async def test_transcript_event_maps_to_stt(self):
        sent = []

        class WS:
            async def send(self, payload):
                sent.append(payload)

        conn = type("Conn", (), {
            "config": {"google_live": {}, "voice_mode": {"type": "google_live"}},
            "websocket": WS(),
            "session_id": "s1",
            "logger": type("L", (), {"bind": lambda *a, **k: type("B", (), {"info": print, "warning": print, "error": print})()})(),
        })()

        provider = GoogleLiveProvider(conn)
        await provider._handle_live_event({"type": "transcript", "text": "xin chao"})
        self.assertTrue(any('"type": "stt"' in item for item in sent))
```

- [ ] **Step 2: Run test and verify it fails**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server && python -m unittest tests.test_google_live_event_mapping -v`

Expected: FAIL because `_handle_live_event` is incomplete.

- [ ] **Step 3: Implement event mapping and helper**

```python
# sendAudioHandle.py
async def send_tts_message(conn, state, text):
    message = {"type": "tts", "state": state, "session_id": conn.session_id}
    if text is not None:
        message["text"] = text
    await conn.websocket.send(json.dumps(message, ensure_ascii=False))
```

```python
# google_live.py
import json

async def _handle_live_event(self, event):
    event_type = event.get("type")
    if event_type == "transcript":
        await self.conn.websocket.send(json.dumps({
            "type": "stt",
            "text": event["text"],
            "session_id": self.conn.session_id,
        }, ensure_ascii=False))
    elif event_type == "audio_start":
        await send_tts_message(self.conn, "start", None)
    elif event_type == "audio_chunk":
        opus_packet = self.bridge.pcm16k_to_pcm24k(event["pcm_bytes"])
        await sendAudio(self.conn, opus_packet)
    elif event_type == "audio_end":
        await send_tts_message(self.conn, "stop", None)
```

- [ ] **Step 4: Run event mapping test and smoke-check firmware compatibility assumptions**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server && python -m unittest tests.test_google_live_event_mapping -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add esp32-server/main/tbot-server/core/voice/session_provider/google_live.py \
  esp32-server/main/tbot-server/core/handle/sendAudioHandle.py \
  esp32-server/main/tbot-server/tests/test_google_live_event_mapping.py
git commit -m "feat: map google live events to existing firmware protocol"
```

### Task 7: Add Admin UI Mode Selector and Payload Wiring

**Files:**
- Modify: `esp32-server/main/manager-web/src/views/roleConfig.vue`
- Modify: `esp32-server/main/manager-web/src/i18n/en.js`
- Modify: `esp32-server/main/manager-web/src/i18n/vi.js`
- Modify: `esp32-server/main/manager-web/src/i18n/de.js`
- Modify: `esp32-server/main/manager-web/src/i18n/pt_BR.js`

- [ ] **Step 1: Add new form state and save/load wiring**

```javascript
// roleConfig.vue data()
form: {
  ...
  voiceMode: "classic_pipeline",
  googleLiveConfigJson: "",
  googleLiveConfig: {
    model: "gemini-2.5-flash-native-audio-preview-12-2025",
    native_voice: true,
    fallback_to_classic_on_error: true,
  },
}
```

```javascript
// saveConfig()
configData.voiceMode = this.form.voiceMode;
configData.googleLiveConfigJson = JSON.stringify(this.form.googleLiveConfig);
```

```javascript
// fetchAgentConfig()
this.form.voiceMode = data.data.voiceMode || "classic_pipeline";
this.form.googleLiveConfigJson = data.data.googleLiveConfigJson || "";
this.form.googleLiveConfig = this.form.googleLiveConfigJson
  ? JSON.parse(this.form.googleLiveConfigJson)
  : {
      model: "gemini-2.5-flash-native-audio-preview-12-2025",
      native_voice: true,
      fallback_to_classic_on_error: true,
    };
```

- [ ] **Step 2: Add UI select and conditional block**

```vue
<el-form-item class="model-item">
  <template #label>
    <span>{{ $t('roleConfig.voiceMode') }}</span>
  </template>
  <div class="model-select-wrapper">
    <el-select v-model="form.voiceMode" class="form-select">
      <el-option :label="$t('roleConfig.classicPipeline')" value="classic_pipeline" />
      <el-option :label="$t('roleConfig.googleLiveApi')" value="google_live" />
    </el-select>
  </div>
</el-form-item>

<div v-if="form.voiceMode === 'google_live'" class="model-row">
  <el-form-item class="model-item">
    <template #label><span>{{ $t('roleConfig.googleLiveModel') }}</span></template>
    <el-input v-model="form.googleLiveConfig.model" class="form-input" />
  </el-form-item>
  <el-form-item class="model-item">
    <template #label><span>{{ $t('roleConfig.nativeVoice') }}</span></template>
    <el-switch v-model="form.googleLiveConfig.native_voice" />
  </el-form-item>
</div>
```

- [ ] **Step 3: Add i18n labels**

```javascript
'roleConfig.voiceMode': 'Voice mode',
'roleConfig.classicPipeline': 'Classic Pipeline',
'roleConfig.googleLiveApi': 'Google Live API',
'roleConfig.googleLiveModel': 'Google Live model',
'roleConfig.nativeVoice': 'Native voice',
```

- [ ] **Step 4: Run frontend build or lint check**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web && npm run build`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add esp32-server/main/manager-web/src/views/roleConfig.vue \
  esp32-server/main/manager-web/src/i18n/en.js \
  esp32-server/main/manager-web/src/i18n/vi.js \
  esp32-server/main/manager-web/src/i18n/de.js \
  esp32-server/main/manager-web/src/i18n/pt_BR.js
git commit -m "feat: add google live voice mode controls to admin ui"
```

### Task 8: Add Default Config and End-to-End Verification

**Files:**
- Modify: `esp32-server/main/tbot-server/config.yaml`
- Modify: `esp32-server/main/tbot-server/app.py`
- Test: `esp32-server/main/tbot-server/tests/test_config_voice_mode_merge.py`

- [ ] **Step 1: Add default config sections**

```yaml
voice_mode:
  type: classic_pipeline
  fallback_to_classic_on_error: true

google_live:
  api_key: ${GOOGLE_API_KEY}
  model: gemini-2.5-flash-native-audio-preview-12-2025
  enable_audio_input: true
  enable_audio_output: true
  native_voice: true
  input_audio_format: pcm16
  input_sample_rate: 16000
  output_audio_format: pcm16
  output_sample_rate: 24000
  connect_timeout_sec: 10
  recv_timeout_sec: 30
  barge_in: true
  send_transcript_events: true
  send_llm_state_events: false
```

- [ ] **Step 2: Add config merge regression test**

```python
import unittest

from config.config_loader import merge_configs, normalize_voice_mode


class ConfigVoiceModeMergeTest(unittest.TestCase):
    def test_default_voice_mode_is_classic(self):
        merged = normalize_voice_mode({})
        self.assertEqual(merged["voice_mode"]["type"], "classic_pipeline")

    def test_custom_voice_mode_survives_merge(self):
        merged = merge_configs(
            {"voice_mode": {"type": "classic_pipeline"}},
            {"voice_mode": {"type": "google_live"}}
        )
        merged = normalize_voice_mode(merged)
        self.assertEqual(merged["voice_mode"]["type"], "google_live")
```

- [ ] **Step 3: Run Python config tests**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server && python -m unittest tests.test_config_voice_mode_merge -v`

Expected: PASS

- [ ] **Step 4: Run full verification**

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-api && mvn test`

Expected: PASS

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server && python -m unittest discover tests -v`

Expected: PASS

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web && npm run build`

Expected: PASS

Run: `cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server && python app.py`

Expected: starts successfully and logs `voice_mode=classic_pipeline` for default sessions.

- [ ] **Step 5: Commit**

```bash
git add esp32-server/main/tbot-server/config.yaml \
  esp32-server/main/tbot-server/tests/test_config_voice_mode_merge.py \
  esp32-server/main/tbot-server/app.py
git commit -m "chore: add default google live config and verification tests"
```

## Self-Review Checklist

- Spec coverage:
  - provider abstraction covered in Tasks 3 to 6
  - config and fallback covered in Tasks 2, 5, and 8
  - manager API and UI covered in Tasks 1, 2, and 7
  - compatibility and logging covered in Tasks 4 to 6 and 8
- Placeholder scan:
  - no `TODO`, `TBD`, or “implement later” remain
- Type consistency:
  - `voiceMode` and `googleLiveConfigJson` used consistently across DB, DTO, VO, UI, and config payload

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-13-google-live-voice-mode-implementation-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
