package tbot.modules.device;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;

import java.util.List;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.core.JsonFactory;
import com.fasterxml.jackson.core.json.JsonReadFeature;

import tbot.modules.device.controller.DeviceChildProfileInternalController;
import tbot.modules.device.dto.DeviceChildProfileProjectionDTO;
import tbot.modules.device.service.DeviceChildProfileProjectionService;
import tbot.modules.device.service.DeviceChildProfileProjectionService.Outcome;
import tbot.modules.device.service.DeviceChildProfileProjectionService.ProjectionResult;
import tbot.modules.device.service.DeviceChildProfileProjectionService.StoredProfile;
import tbot.modules.device.service.DeviceChildProfileProjectionService.ProjectionConflictException;
import tbot.common.exception.RenExceptionHandler;
import tbot.modules.security.config.WebMvcConfig;

class DeviceChildProfileInternalControllerTest {
    @Test
    void endpointUsesDedicatedInternalDeviceBoundPath() throws Exception {
        RequestMapping root = DeviceChildProfileInternalController.class.getAnnotation(RequestMapping.class);
        PutMapping method = DeviceChildProfileInternalController.class
                .getMethod("replace", String.class, DeviceChildProfileProjectionDTO.class)
                .getAnnotation(PutMapping.class);
        assertEquals(List.of("/internal/devices"), List.of(root.value()));
        assertEquals(List.of("/{deviceId}/child-profile"), List.of(method.value()));
    }

    @Test
    void dtoRejectsUnknownFields() {
        String json = "{\"mode\":\"clear\",\"revision\":1,\"payloadHash\":\"" + "0".repeat(64) + "\",\"profile\":null,\"unexpected\":true}";
        assertThrows(Exception.class, () -> new ObjectMapper().readValue(json, DeviceChildProfileProjectionDTO.class));
    }

    @Test
    void dtoRejectsMissingProfileFieldEvenWhenNullable() {
        String json = "{\"mode\":\"clear\",\"revision\":1,\"payloadHash\":\"" + "0".repeat(64) + "\"}";
        assertThrows(Exception.class, () -> new ObjectMapper().readValue(json, DeviceChildProfileProjectionDTO.class));
    }

    @ParameterizedTest
    @ValueSource(strings = { "1.9", "1e-1", "\"1\"", "-1", "9007199254740992", "9223372036854775808" })
    void dtoRejectsNonIntegerOrOutOfRangeRevision(String revision) {
        String json = "{\"mode\":\"clear\",\"revision\":" + revision
                + ",\"payloadHash\":\"" + "0".repeat(64) + "\",\"profile\":null}";
        assertThrows(Exception.class, () -> new ObjectMapper().readValue(json, DeviceChildProfileProjectionDTO.class));
    }

    @ParameterizedTest
    @ValueSource(strings = { "2018.9", "2.018e3", "\"2018\"", "2147483648", "-2147483649" })
    void dtoRejectsNonIntegerOrOutOfRangeBirthYear(String birthYear) {
        String json = replaceJson(birthYear);
        assertThrows(Exception.class, () -> new ObjectMapper().readValue(json, DeviceChildProfileProjectionDTO.class));
    }

    @ParameterizedTest
    @ValueSource(strings = { "NaN", "Infinity", "-Infinity" })
    void dtoRejectsNonFiniteNumbersEvenWhenParserAllowsThem(String number) {
        ObjectMapper permissive = new ObjectMapper(JsonFactory.builder()
                .enable(JsonReadFeature.ALLOW_NON_NUMERIC_NUMBERS)
                .build());
        assertThrows(Exception.class, () -> permissive.readValue(replaceJson(number), DeviceChildProfileProjectionDTO.class));
    }

    @Test
    void oversizedProfileIsReturnedAsBadRequestInsteadOfDatabaseFailure() throws Exception {
        DeviceChildProfileProjectionService service = mock(DeviceChildProfileProjectionService.class);
        when(service.apply(eq("device-1"), any())).thenThrow(new IllegalArgumentException("displayName exceeds storage capacity"));
        MockMvc mvc = mvc(service);
        String json = replaceJson("2018").replace("\"An\"", "\"" + "x".repeat(65) + "\"");

        mvc.perform(put("/internal/devices/device-1/child-profile")
                .contentType(MediaType.APPLICATION_JSON)
                .content(json))
                .andExpect(status().isBadRequest());
    }

    @Test
    void beanValidationFailureOverridesGlobalHandlerWithHttp400() throws Exception {
        String json = "{\"mode\":\"invalid\",\"revision\":1,\"payloadHash\":\""
                + "0".repeat(64) + "\",\"profile\":null}";

        mvc(mock(DeviceChildProfileProjectionService.class)).perform(
                put("/internal/devices/device-1/child-profile")
                        .contentType(MediaType.APPLICATION_JSON).content(json))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").isNumber());
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "{",
            "{\"mode\":\"clear\",\"revision\":1.5,\"payloadHash\":\"HASH\",\"profile\":null}",
            "{\"mode\":\"clear\",\"revision\":\"1\",\"payloadHash\":\"HASH\",\"profile\":null}"
    })
    void unreadableProjectionJsonOverridesGlobalHandlerWithHttp400(String template) throws Exception {
        String json = template.replace("HASH", "0".repeat(64));
        mvc(mock(DeviceChildProfileProjectionService.class)).perform(
                put("/internal/devices/device-1/child-profile")
                        .contentType(MediaType.APPLICATION_JSON).content(json))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").isNumber());
    }

    @Test
    void projectionConflictRemainsHttp409WithGlobalHandlerInstalled() throws Exception {
        DeviceChildProfileProjectionService service = mock(DeviceChildProfileProjectionService.class);
        when(service.apply(eq("device-1"), any())).thenThrow(new ProjectionConflictException("conflict"));
        String json = "{\"mode\":\"clear\",\"revision\":1,\"payloadHash\":\""
                + "0".repeat(64) + "\",\"profile\":null}";

        mvc(service).perform(put("/internal/devices/device-1/child-profile")
                .contentType(MediaType.APPLICATION_JSON).content(json))
                .andExpect(status().isConflict());
    }

    @Test
    void successResponseIsTheFullStoredProjectionReturnedByTheService() throws Exception {
        DeviceChildProfileProjectionService service = mock(DeviceChildProfileProjectionService.class);
        String hash = "0".repeat(64);
        when(service.apply(eq("device-1"), any())).thenReturn(new ProjectionResult(
                Outcome.NO_OP, "device-1", 7, hash, "replace",
                new StoredProfile("123e4567-e89b-12d3-a456-426614174000", "Stored An", 2017,
                        List.of("art", "robots"), "visual", "advanced", "engineer")));

        mvc(service).perform(put("/internal/devices/device-1/child-profile")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"mode\":\"clear\",\"revision\":7,\"payloadHash\":\"" + hash + "\",\"profile\":null}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.outcome").value("NO_OP"))
                .andExpect(jsonPath("$.data.deviceId").value("device-1"))
                .andExpect(jsonPath("$.data.revision").value(7))
                .andExpect(jsonPath("$.data.payloadHash").value(hash))
                .andExpect(jsonPath("$.data.mode").value("replace"))
                .andExpect(jsonPath("$.data.profile.childProfileId").value("123e4567-e89b-12d3-a456-426614174000"))
                .andExpect(jsonPath("$.data.profile.displayName").value("Stored An"))
                .andExpect(jsonPath("$.data.profile.birthYear").value(2017))
                .andExpect(jsonPath("$.data.profile.interests[0]").value("art"))
                .andExpect(jsonPath("$.data.profile.learningStyle").value("visual"))
                .andExpect(jsonPath("$.data.profile.vocabularyLevel").value("advanced"))
                .andExpect(jsonPath("$.data.profile.parentCareer").value("engineer"))
                .andExpect(jsonPath("$.data.profile.childAge").doesNotExist());
    }

    @Test
    void clearResponseContainsStoredNullProfile() throws Exception {
        DeviceChildProfileProjectionService service = mock(DeviceChildProfileProjectionService.class);
        String hash = "0".repeat(64);
        when(service.apply(eq("device-1"), any())).thenReturn(
                new ProjectionResult(Outcome.APPLIED, "device-1", 8, hash, "clear", null));

        mvc(service).perform(put("/internal/devices/device-1/child-profile")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"mode\":\"clear\",\"revision\":8,\"payloadHash\":\"" + hash + "\",\"profile\":null}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.mode").value("clear"))
                .andExpect(jsonPath("$.data.profile").isEmpty());
    }

    @Test
    void productionJsonConverterKeepsRevisionNumericAndExplicitClearProfileNull() throws Exception {
        DeviceChildProfileProjectionService service = mock(DeviceChildProfileProjectionService.class);
        String hash = "0".repeat(64);
        when(service.apply(eq("device-1"), any())).thenReturn(
                new ProjectionResult(Outcome.APPLIED, "device-1", 8, hash, "clear", null));

        String body = MockMvcBuilders.standaloneSetup(new DeviceChildProfileInternalController(service))
                .setControllerAdvice(new RenExceptionHandler())
                .setMessageConverters(new WebMvcConfig().jackson2HttpMessageConverter())
                .build()
                .perform(put("/internal/devices/device-1/child-profile")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"mode\":\"clear\",\"revision\":8,\"payloadHash\":\""
                                + hash + "\",\"profile\":null}"))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();

        var data = new ObjectMapper().readTree(body).path("data");
        assertTrue(data.path("revision").isIntegralNumber(), body);
        assertTrue(data.has("profile") && data.get("profile").isNull(), body);
    }

    private static MockMvc mvc(DeviceChildProfileProjectionService service) {
        return MockMvcBuilders.standaloneSetup(new DeviceChildProfileInternalController(service))
                .setControllerAdvice(new RenExceptionHandler())
                .build();
    }

    private static String replaceJson(String birthYear) {
        return "{\"mode\":\"replace\",\"revision\":1,\"payloadHash\":\"" + "0".repeat(64)
                + "\",\"profile\":{\"childProfileId\":\"123e4567-e89b-12d3-a456-426614174000\","
                + "\"displayName\":\"An\",\"birthYear\":" + birthYear
                + ",\"interests\":[],\"learningStyle\":null,\"vocabularyLevel\":null,\"parentCareer\":null}}";
    }
}
