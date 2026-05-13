package tbot.modules.agent;

import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.http.MediaType.APPLICATION_JSON;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.Mockito;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import com.fasterxml.jackson.databind.ObjectMapper;

import tbot.common.redis.RedisUtils;
import tbot.modules.agent.controller.AgentController;
import tbot.modules.agent.dto.AgentUpdateDTO;
import tbot.modules.agent.service.AgentChatAudioService;
import tbot.modules.agent.service.AgentChatHistoryService;
import tbot.modules.agent.service.AgentChatSummaryService;
import tbot.modules.agent.service.AgentContextProviderService;
import tbot.modules.agent.service.AgentPluginMappingService;
import tbot.modules.agent.service.AgentService;
import tbot.modules.agent.service.AgentTagService;
import tbot.modules.agent.service.AgentTemplateService;
import tbot.modules.agent.vo.AgentInfoVO;
import tbot.modules.correctword.service.CorrectWordFileService;

class AgentControllerVoiceModeTest {

    private final AgentService agentService = Mockito.mock(AgentService.class);
    private final AgentTemplateService agentTemplateService = Mockito.mock(AgentTemplateService.class);
    private final tbot.modules.device.service.DeviceService deviceService = Mockito.mock(
            tbot.modules.device.service.DeviceService.class);
    private final AgentChatHistoryService agentChatHistoryService = Mockito.mock(AgentChatHistoryService.class);
    private final AgentChatAudioService agentChatAudioService = Mockito.mock(AgentChatAudioService.class);
    private final AgentPluginMappingService agentPluginMappingService = Mockito.mock(AgentPluginMappingService.class);
    private final AgentContextProviderService agentContextProviderService = Mockito.mock(
            AgentContextProviderService.class);
    private final AgentChatSummaryService agentChatSummaryService = Mockito.mock(AgentChatSummaryService.class);
    private final RedisUtils redisUtils = Mockito.mock(RedisUtils.class);
    private final AgentTagService agentTagService = Mockito.mock(AgentTagService.class);
    private final CorrectWordFileService correctWordFileService = Mockito.mock(CorrectWordFileService.class);
    private final ObjectMapper objectMapper = new ObjectMapper();

    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        Mockito.reset(
                agentService,
                agentTemplateService,
                deviceService,
                agentChatHistoryService,
                agentChatAudioService,
                agentPluginMappingService,
                agentContextProviderService,
                agentChatSummaryService,
                redisUtils,
                agentTagService,
                correctWordFileService);

        AgentController agentController = new AgentController(
                agentService,
                agentTemplateService,
                deviceService,
                agentChatHistoryService,
                agentChatAudioService,
                agentPluginMappingService,
                agentContextProviderService,
                agentChatSummaryService,
                redisUtils,
                agentTagService,
                correctWordFileService);

        mockMvc = MockMvcBuilders.standaloneSetup(agentController)
                .setMessageConverters(new MappingJackson2HttpMessageConverter(objectMapper))
                .build();
    }

    @Test
    @DisplayName("GET /agent/{id} returns voice mode fields in JSON response")
    void getAgentByIdReturnsVoiceModeFields() throws Exception {
        AgentInfoVO agent = new AgentInfoVO();
        agent.setId("agent-voice");
        agent.setVoiceMode("google_live");
        agent.setGoogleLiveConfigJson("{\"voice\":\"Kore\"}");
        when(agentService.getAgentById("agent-voice")).thenReturn(agent);

        mockMvc.perform(get("/agent/{id}", "agent-voice"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.msg").value("success"))
                .andExpect(jsonPath("$.data.id").value("agent-voice"))
                .andExpect(jsonPath("$.data.voiceMode").value("google_live"))
                .andExpect(jsonPath("$.data.googleLiveConfigJson").value("{\"voice\":\"Kore\"}"));
    }

    @Test
    @DisplayName("PUT /agent/{id} binds voice mode fields from JSON body")
    void updateAgentBindsVoiceModeFields() throws Exception {
        String requestBody = """
                {
                  "voiceMode": "google_live",
                  "googleLiveConfigJson": "{\\"voice\\":\\"Kore\\"}"
                }
                """;

        mockMvc.perform(put("/agent/{id}", "agent-voice")
                        .contentType(APPLICATION_JSON)
                        .content(requestBody))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.msg").value("success"));

        ArgumentCaptor<AgentUpdateDTO> updateCaptor = ArgumentCaptor.forClass(AgentUpdateDTO.class);
        verify(agentService).updateAgentById(eq("agent-voice"), updateCaptor.capture());
        AgentUpdateDTO captured = updateCaptor.getValue();
        org.junit.jupiter.api.Assertions.assertEquals("google_live", captured.getVoiceMode());
        org.junit.jupiter.api.Assertions.assertEquals("{\"voice\":\"Kore\"}", captured.getGoogleLiveConfigJson());
    }
}
