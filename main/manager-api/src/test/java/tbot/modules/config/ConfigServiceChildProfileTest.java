package tbot.modules.config;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.HashMap;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import tbot.common.redis.RedisUtils;
import tbot.modules.agent.service.AgentContextProviderService;
import tbot.modules.agent.service.AgentMcpAccessPointService;
import tbot.modules.agent.service.AgentPluginMappingService;
import tbot.modules.agent.service.AgentService;
import tbot.modules.agent.service.AgentTemplateService;
import tbot.modules.agent.dao.AgentVoicePrintDao;
import tbot.modules.agent.vo.AgentInfoVO;
import tbot.modules.config.service.impl.ConfigServiceImpl;
import tbot.modules.correctword.service.CorrectWordFileService;
import tbot.modules.device.entity.DeviceEntity;
import tbot.modules.device.service.DeviceService;
import tbot.modules.model.service.ModelConfigService;
import tbot.modules.sys.service.SysParamsService;
import tbot.modules.timbre.service.TimbreService;
import tbot.modules.voiceclone.service.VoiceCloneService;

class ConfigServiceChildProfileTest {

    @Test
    @DisplayName("getAgentModels includes child profile for the connecting device")
    void getAgentModelsIncludesChildProfileForDevice() {
        SysParamsService sysParamsService = mock(SysParamsService.class);
        DeviceService deviceService = mock(DeviceService.class);
        ModelConfigService modelConfigService = mock(ModelConfigService.class);
        AgentService agentService = mock(AgentService.class);
        AgentTemplateService agentTemplateService = mock(AgentTemplateService.class);
        RedisUtils redisUtils = mock(RedisUtils.class);
        TimbreService timbreService = mock(TimbreService.class);
        AgentPluginMappingService pluginMappingService = mock(AgentPluginMappingService.class);
        AgentMcpAccessPointService mcpAccessPointService = mock(AgentMcpAccessPointService.class);
        AgentContextProviderService contextProviderService = mock(AgentContextProviderService.class);
        VoiceCloneService voiceCloneService = mock(VoiceCloneService.class);
        AgentVoicePrintDao voicePrintDao = mock(AgentVoicePrintDao.class);
        CorrectWordFileService correctWordFileService = mock(CorrectWordFileService.class);

        ConfigServiceImpl service = new ConfigServiceImpl(
                sysParamsService,
                deviceService,
                modelConfigService,
                agentService,
                agentTemplateService,
                redisUtils,
                timbreService,
                pluginMappingService,
                mcpAccessPointService,
                contextProviderService,
                voiceCloneService,
                voicePrintDao,
                correctWordFileService);

        DeviceEntity device = new DeviceEntity();
        device.setId("device-1");
        device.setMacAddress("AA:BB:CC:DD:EE:FF");
        device.setAlias("Robot phong ngu");
        device.setChildName("Bong");
        device.setChildAge(6);
        device.setChildInterests("animals, space");
        device.setLearningStyle("visual");
        device.setVocabularyLevel("beginner");
        device.setParentCareer("teacher");
        device.setAgentId("agent-1");

        AgentInfoVO agent = new AgentInfoVO();
        agent.setId("agent-1");
        agent.setAgentName("TBOT");
        agent.setSystemPrompt("Xin chao {{assistant_name}}");
        agent.setIntentModelId("Intent_nointent");

        when(redisUtils.get(anyString())).thenReturn(null);
        when(deviceService.getDeviceByMacAddress("AA:BB:CC:DD:EE:FF")).thenReturn(device);
        when(agentService.getAgentById("agent-1")).thenReturn(agent);
        when(sysParamsService.getValue(anyString(), org.mockito.ArgumentMatchers.eq(true))).thenReturn("");

        Map<String, Object> result = service.getAgentModels("AA:BB:CC:DD:EE:FF", new HashMap<>());

        Map<?, ?> childProfile = assertInstanceOf(Map.class, result.get("child_profile"));
        assertEquals("device-1", childProfile.get("device_id"));
        assertEquals("Robot phong ngu", childProfile.get("device_alias"));
        assertEquals("Bong", childProfile.get("child_name"));
        assertEquals(6, childProfile.get("child_age"));
        assertEquals(java.util.List.of("animals", "space"), childProfile.get("interests"));
        assertEquals("visual", childProfile.get("learning_style"));
        assertEquals("beginner", childProfile.get("vocabulary_level"));
        assertEquals("teacher", childProfile.get("parent_career"));

        device.setChildInterestsJson("[\"\",\"science, technology\"]");
        Map<String, Object> jsonResult = service.getAgentModels(
                "AA:BB:CC:DD:EE:FF", new HashMap<>());
        Map<?, ?> jsonProfile = assertInstanceOf(Map.class, jsonResult.get("child_profile"));
        assertEquals(java.util.List.of("", "science, technology"), jsonProfile.get("interests"));

        device.setChildInterestsJson(null);
        device.setChildInterests("[robotics],music");
        Map<String, Object> malformedLegacyResult = service.getAgentModels(
                "AA:BB:CC:DD:EE:FF", new HashMap<>());
        Map<?, ?> malformedLegacyProfile = assertInstanceOf(
                Map.class, malformedLegacyResult.get("child_profile"));
        assertEquals(java.util.List.of("[robotics]", "music"), malformedLegacyProfile.get("interests"));
    }
}
