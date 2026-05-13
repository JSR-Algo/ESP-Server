package tbot.modules.agent;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doReturn;
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

import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.redis.RedisUtils;
import tbot.modules.agent.controller.AgentController;
import tbot.modules.agent.dto.AgentUpdateDTO;
import tbot.modules.agent.entity.AgentEntity;
import tbot.modules.agent.service.AgentChatAudioService;
import tbot.modules.agent.service.AgentChatHistoryService;
import tbot.modules.agent.service.AgentChatSummaryService;
import tbot.modules.agent.service.AgentContextProviderService;
import tbot.modules.agent.service.AgentPluginMappingService;
import tbot.modules.agent.service.AgentService;
import tbot.modules.agent.service.AgentTagService;
import tbot.modules.agent.service.AgentTemplateService;
import tbot.modules.agent.service.impl.AgentServiceImpl;
import tbot.modules.agent.dao.AgentDao;
import tbot.modules.agent.dao.AgentTagDao;
import tbot.modules.agent.vo.AgentInfoVO;
import tbot.modules.correctword.service.CorrectWordFileService;
import tbot.modules.device.service.DeviceService;
import tbot.modules.model.service.ModelConfigService;
import tbot.modules.model.service.ModelProviderService;
import tbot.modules.timbre.service.TimbreService;

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

    @Test
    @DisplayName("Service clears google live config when switching back to classic pipeline")
    void serviceClearsGoogleLiveConfigForClassicPipeline() {
        AgentEntity existing = new AgentEntity();
        existing.setId("agent-voice");
        existing.setVoiceMode("google_live");
        existing.setGoogleLiveConfigJson("{\"voice\":\"Old\"}");

        AgentUpdateDTO dto = new AgentUpdateDTO();
        dto.setVoiceMode("classic_pipeline");
        dto.setGoogleLiveConfigJson("{\"voice\":\"ShouldBeIgnored\"}");

        AgentServiceImpl service = buildAgentServiceSpy(existing);

        service.updateAgentById("agent-voice", dto);

        assertEquals("classic_pipeline", existing.getVoiceMode());
        assertNull(existing.getGoogleLiveConfigJson());
        verify(service).updateById(existing);
    }

    @Test
    @DisplayName("Service rejects invalid google live config JSON before persistence")
    void serviceRejectsInvalidGoogleLiveConfigJson() {
        AgentEntity existing = new AgentEntity();
        existing.setId("agent-voice");

        AgentUpdateDTO dto = new AgentUpdateDTO();
        dto.setVoiceMode("google_live");
        dto.setGoogleLiveConfigJson("{invalid");

        AgentServiceImpl service = buildAgentServiceSpy(existing);

        RenException exception = assertThrows(RenException.class, () -> service.updateAgentById("agent-voice", dto));

        assertEquals(ErrorCode.PARAM_JSON_INVALID, exception.getCode());
        assertEquals("googleLiveConfigJson must be valid JSON object", exception.getMessage());
    }

    private AgentServiceImpl buildAgentServiceSpy(AgentEntity existing) {
        AgentServiceImpl service = Mockito.spy(new AgentServiceImpl(
                Mockito.mock(AgentDao.class),
                Mockito.mock(AgentTagDao.class),
                Mockito.mock(TimbreService.class),
                Mockito.mock(ModelConfigService.class),
                redisUtils,
                Mockito.mock(DeviceService.class),
                agentPluginMappingService,
                agentChatHistoryService,
                agentTemplateService,
                Mockito.mock(ModelProviderService.class),
                agentContextProviderService,
                agentTagService,
                correctWordFileService));

        doReturn(existing).when(service).getAgentById("agent-voice");
        doReturn(true).when(service).updateById(any(AgentEntity.class));
        return service;
    }
}
