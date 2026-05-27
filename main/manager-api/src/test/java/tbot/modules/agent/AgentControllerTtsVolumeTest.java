package tbot.modules.agent;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.Mockito;

import tbot.common.redis.RedisUtils;
import tbot.common.utils.Result;
import tbot.modules.agent.controller.AgentController;
import tbot.modules.agent.dto.AgentTtsVolumeDTO;
import tbot.modules.agent.dto.AgentUpdateDTO;
import tbot.modules.agent.service.AgentChatAudioService;
import tbot.modules.agent.service.AgentChatHistoryService;
import tbot.modules.agent.service.AgentChatSummaryService;
import tbot.modules.agent.service.AgentContextProviderService;
import tbot.modules.agent.service.AgentPluginMappingService;
import tbot.modules.agent.service.AgentService;
import tbot.modules.agent.service.AgentTagService;
import tbot.modules.agent.service.AgentTemplateService;
import tbot.modules.correctword.service.CorrectWordFileService;
import tbot.modules.device.entity.DeviceEntity;
import tbot.modules.device.service.DeviceService;

class AgentControllerTtsVolumeTest {

    private AgentController agentController;

    private DeviceService deviceService;

    private AgentService agentService;

    @BeforeEach
    void setUp() {
        deviceService = mock(DeviceService.class);
        agentService = mock(AgentService.class);
        agentController = new AgentController(
                agentService,
                mock(AgentTemplateService.class),
                deviceService,
                mock(AgentChatHistoryService.class),
                mock(AgentChatAudioService.class),
                mock(AgentPluginMappingService.class),
                mock(AgentContextProviderService.class),
                mock(AgentChatSummaryService.class),
                mock(RedisUtils.class),
                mock(AgentTagService.class),
                mock(CorrectWordFileService.class));
    }

    @Test
    @DisplayName("PUT /agent/ttsVolume/{macAddress} updates persistent tts volume for mapped agent")
    void updateTtsVolumeByMacAddress() {
        DeviceEntity device = new DeviceEntity();
        device.setAgentId("agent-123");
        when(deviceService.getDeviceByMacAddress("AA:BB:CC:DD:EE:FF")).thenReturn(device);

        AgentTtsVolumeDTO dto = new AgentTtsVolumeDTO();
        dto.setTtsVolume(55);

        Result<Void> result = agentController.updateTtsVolumeByDeviceId("AA:BB:CC:DD:EE:FF", dto);

        assertEquals(0, result.getCode());
        assertEquals("success", result.getMsg());
        assertNull(result.getData());

        ArgumentCaptor<AgentUpdateDTO> updateCaptor = ArgumentCaptor.forClass(AgentUpdateDTO.class);
        verify(agentService).updateAgentById(eq("agent-123"), updateCaptor.capture());
        assertEquals(55, updateCaptor.getValue().getTtsVolume());
    }

    @Test
    @DisplayName("PUT /agent/ttsVolume/{macAddress} returns empty result when device is unknown")
    void updateTtsVolumeByUnknownMacAddress() {
        when(deviceService.getDeviceByMacAddress("00:00:00:00:00:00")).thenReturn(null);

        AgentTtsVolumeDTO dto = new AgentTtsVolumeDTO();
        dto.setTtsVolume(20);

        Result<Void> result = agentController.updateTtsVolumeByDeviceId("00:00:00:00:00:00", dto);

        assertEquals(0, result.getCode());
        assertEquals("success", result.getMsg());
        assertNull(result.getData());
        verifyNoInteractions(agentService);
    }
}
