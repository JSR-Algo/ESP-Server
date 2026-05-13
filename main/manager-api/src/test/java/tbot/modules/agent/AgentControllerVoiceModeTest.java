package tbot.modules.agent;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.Mockito;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.test.context.ActiveProfiles;

import tbot.common.redis.RedisUtils;
import tbot.common.utils.Result;
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
import tbot.modules.device.entity.DeviceEntity;
import tbot.modules.device.service.DeviceService;

@SpringBootTest(classes = AgentControllerVoiceModeTest.TestConfig.class)
@ActiveProfiles("dev")
class AgentControllerVoiceModeTest {

    @Autowired
    private AgentController agentController;

    @Autowired
    private AgentService agentService;

    @Autowired
    private DeviceService deviceService;

    @BeforeEach
    void resetMocks() {
        Mockito.reset(agentService, deviceService);
    }

    @Test
    @DisplayName("GET /agent/{id} includes voice mode fields")
    void getAgentByIdIncludesVoiceModeFields() {
        AgentInfoVO agent = new AgentInfoVO();
        agent.setId("agent-voice");
        agent.setVoiceMode("google_live");
        agent.setGoogleLiveConfigJson("{\"voice\":\"Kore\"}");
        when(agentService.getAgentById("agent-voice")).thenReturn(agent);

        Result<AgentInfoVO> result = agentController.getAgentById("agent-voice");

        assertEquals(0, result.getCode());
        assertEquals("success", result.getMsg());
        assertEquals("google_live", result.getData().getVoiceMode());
        assertEquals("{\"voice\":\"Kore\"}", result.getData().getGoogleLiveConfigJson());
    }

    @Test
    @DisplayName("PUT /agent/{id} accepts voice mode fields")
    void updateAgentAcceptsVoiceModeFields() {
        AgentUpdateDTO dto = new AgentUpdateDTO();
        dto.setVoiceMode("google_live");
        dto.setGoogleLiveConfigJson("{\"voice\":\"Kore\"}");

        Result<Void> result = agentController.update("agent-voice", dto);

        assertEquals(0, result.getCode());
        assertEquals("success", result.getMsg());
        assertNull(result.getData());

        ArgumentCaptor<AgentUpdateDTO> updateCaptor = ArgumentCaptor.forClass(AgentUpdateDTO.class);
        verify(agentService).updateAgentById(eq("agent-voice"), updateCaptor.capture());
        assertEquals("google_live", updateCaptor.getValue().getVoiceMode());
        assertEquals("{\"voice\":\"Kore\"}", updateCaptor.getValue().getGoogleLiveConfigJson());
    }

    @TestConfiguration
    static class TestConfig {
        @Bean
        AgentController agentController(
                AgentService agentService,
                AgentTemplateService agentTemplateService,
                DeviceService deviceService,
                AgentChatHistoryService agentChatHistoryService,
                AgentChatAudioService agentChatAudioService,
                AgentPluginMappingService agentPluginMappingService,
                AgentContextProviderService agentContextProviderService,
                AgentChatSummaryService agentChatSummaryService,
                RedisUtils redisUtils,
                AgentTagService agentTagService,
                CorrectWordFileService correctWordFileService) {
            return new AgentController(
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
        }

        @Bean
        AgentService agentService() {
            return Mockito.mock(AgentService.class);
        }

        @Bean
        AgentTemplateService agentTemplateService() {
            return Mockito.mock(AgentTemplateService.class);
        }

        @Bean
        DeviceService deviceService() {
            return Mockito.mock(DeviceService.class);
        }

        @Bean
        AgentChatHistoryService agentChatHistoryService() {
            return Mockito.mock(AgentChatHistoryService.class);
        }

        @Bean
        AgentChatAudioService agentChatAudioService() {
            return Mockito.mock(AgentChatAudioService.class);
        }

        @Bean
        AgentPluginMappingService agentPluginMappingService() {
            return Mockito.mock(AgentPluginMappingService.class);
        }

        @Bean
        AgentContextProviderService agentContextProviderService() {
            return Mockito.mock(AgentContextProviderService.class);
        }

        @Bean
        AgentChatSummaryService agentChatSummaryService() {
            return Mockito.mock(AgentChatSummaryService.class);
        }

        @Bean
        RedisUtils redisUtils() {
            return Mockito.mock(RedisUtils.class);
        }

        @Bean
        AgentTagService agentTagService() {
            return Mockito.mock(AgentTagService.class);
        }

        @Bean
        CorrectWordFileService correctWordFileService() {
            return Mockito.mock(CorrectWordFileService.class);
        }
    }
}
