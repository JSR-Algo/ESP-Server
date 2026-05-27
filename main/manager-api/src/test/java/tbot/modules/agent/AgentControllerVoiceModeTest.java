package tbot.modules.agent;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.springframework.http.MediaType.APPLICATION_JSON;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.Locale;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.context.ApplicationContext;
import org.springframework.context.MessageSource;
import org.springframework.context.MessageSourceResolvable;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import com.fasterxml.jackson.databind.ObjectMapper;

import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.utils.SpringContextUtils;
import tbot.modules.agent.controller.AgentController;
import tbot.modules.agent.dao.AgentDao;
import tbot.modules.agent.dao.AgentTagDao;
import tbot.modules.agent.dto.AgentUpdateDTO;
import tbot.modules.agent.entity.AgentEntity;
import tbot.modules.agent.service.AgentService;
import tbot.modules.agent.service.impl.AgentServiceImpl;
import tbot.modules.agent.vo.AgentInfoVO;
import tbot.modules.correctword.service.CorrectWordFileService;
import tbot.modules.device.service.DeviceService;
import tbot.modules.model.service.ModelConfigService;
import tbot.modules.model.service.ModelProviderService;
import tbot.modules.timbre.service.TimbreService;

class AgentControllerVoiceModeTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    private MockMvc mockMvc;
    private RecordingAgentServiceHandler agentServiceHandler;

    @BeforeEach
    void setUp() {
        final MessageSource messageSource = new MessageSource() {
            @Override
            public String getMessage(String code, Object[] args, String defaultMessage, Locale locale) {
                if (args != null && args.length > 0 && args[0] instanceof String) {
                    return (String) args[0];
                }
                return defaultMessage != null ? defaultMessage : code;
            }

            @Override
            public String getMessage(String code, Object[] args, Locale locale) {
                return getMessage(code, args, code, locale);
            }

            @Override
            public String getMessage(MessageSourceResolvable resolvable, Locale locale) {
                Object[] args = resolvable.getArguments();
                if (args != null && args.length > 0 && args[0] instanceof String) {
                    return (String) args[0];
                }
                String defaultMessage = resolvable.getDefaultMessage();
                if (defaultMessage != null) {
                    return defaultMessage;
                }
                String[] codes = resolvable.getCodes();
                return codes != null && codes.length > 0 ? codes[0] : "";
            }
        };

        InvocationHandler applicationContextHandler = new InvocationHandler() {
            @Override
            public Object invoke(Object proxy, Method method, Object[] args) {
                if ("getBean".equals(method.getName()) && args != null && args.length == 1
                        && "messageSource".equals(args[0])) {
                    return messageSource;
                }
                throw new UnsupportedOperationException(method.getName());
            }
        };
        SpringContextUtils.applicationContext = (ApplicationContext) Proxy.newProxyInstance(
                ApplicationContext.class.getClassLoader(),
                new Class<?>[] { ApplicationContext.class },
                applicationContextHandler);

        agentServiceHandler = new RecordingAgentServiceHandler();
        AgentService agentService = (AgentService) Proxy.newProxyInstance(
                AgentService.class.getClassLoader(),
                new Class<?>[] { AgentService.class },
                agentServiceHandler);

        AgentController agentController = new AgentController(
                agentService,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null);

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
        agentServiceHandler.agentToReturn = agent;

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

        assertEquals("agent-voice", agentServiceHandler.lastUpdatedAgentId);
        assertEquals("google_live", agentServiceHandler.lastUpdateDto.getVoiceMode());
        assertEquals("{\"voice\":\"Kore\"}", agentServiceHandler.lastUpdateDto.getGoogleLiveConfigJson());
    }

    @Test
    @DisplayName("Service clears google live config when switching back to classic pipeline")
    void serviceClearsGoogleLiveConfigForClassicPipeline() {
        AgentInfoVO existing = new AgentInfoVO();
        existing.setId("agent-voice");
        existing.setVoiceMode("google_live");
        existing.setGoogleLiveConfigJson("{\"voice\":\"Old\"}");

        AgentUpdateDTO dto = new AgentUpdateDTO();
        dto.setVoiceMode("classic_pipeline");
        dto.setGoogleLiveConfigJson("{\"voice\":\"ShouldBeIgnored\"}");

        TestAgentServiceImpl service = new TestAgentServiceImpl(existing);

        service.updateAgentById("agent-voice", dto);

        assertEquals("classic_pipeline", existing.getVoiceMode());
        assertNull(existing.getGoogleLiveConfigJson());
        assertEquals(existing, service.updatedEntity);
    }

    @Test
    @DisplayName("Service rejects invalid google live config JSON before persistence")
    void serviceRejectsInvalidGoogleLiveConfigJson() {
        AgentInfoVO existing = new AgentInfoVO();
        existing.setId("agent-voice");

        AgentUpdateDTO dto = new AgentUpdateDTO();
        dto.setVoiceMode("google_live");
        dto.setGoogleLiveConfigJson("{invalid");

        TestAgentServiceImpl service = new TestAgentServiceImpl(existing);

        RenException exception = assertThrows(RenException.class, () -> service.updateAgentById("agent-voice", dto));

        assertEquals(ErrorCode.PARAM_JSON_INVALID, exception.getCode());
        assertEquals("googleLiveConfigJson must be valid JSON object", exception.getMessage());
    }

    private static final class RecordingAgentServiceHandler implements InvocationHandler {
        private AgentInfoVO agentToReturn;
        private String lastUpdatedAgentId;
        private AgentUpdateDTO lastUpdateDto;

        @Override
        public Object invoke(Object proxy, Method method, Object[] args) {
            return switch (method.getName()) {
                case "getAgentById" -> agentToReturn;
                case "updateAgentById" -> {
                    lastUpdatedAgentId = (String) args[0];
                    lastUpdateDto = (AgentUpdateDTO) args[1];
                    yield null;
                }
                case "toString" -> "RecordingAgentService";
                case "hashCode" -> System.identityHashCode(this);
                case "equals" -> proxy == args[0];
                default -> throw new UnsupportedOperationException(method.getName());
            };
        }
    }

    private static final class TestAgentServiceImpl extends AgentServiceImpl {
        private final AgentInfoVO existing;
        private AgentEntity updatedEntity;

        private TestAgentServiceImpl(AgentInfoVO existing) {
            super(
                    (AgentDao) null,
                    (AgentTagDao) null,
                    (TimbreService) null,
                    (ModelConfigService) null,
                    null,
                    (DeviceService) null,
                    null,
                    null,
                    null,
                    (ModelProviderService) null,
                    null,
                    null,
                    (CorrectWordFileService) null);
            this.existing = existing;
        }

        @Override
        public AgentInfoVO getAgentById(String id) {
            return existing;
        }

        @Override
        public boolean updateById(AgentEntity entity) {
            this.updatedEntity = entity;
            return true;
        }
    }
}
