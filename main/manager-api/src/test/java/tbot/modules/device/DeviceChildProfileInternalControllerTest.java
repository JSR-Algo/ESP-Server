package tbot.modules.device;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

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
        MockMvc mvc = MockMvcBuilders.standaloneSetup(new DeviceChildProfileInternalController(service)).build();
        String json = replaceJson("2018").replace("\"An\"", "\"" + "x".repeat(65) + "\"");

        mvc.perform(put("/internal/devices/device-1/child-profile")
                .contentType(MediaType.APPLICATION_JSON)
                .content(json))
                .andExpect(status().isBadRequest());
    }

    private static String replaceJson(String birthYear) {
        return "{\"mode\":\"replace\",\"revision\":1,\"payloadHash\":\"" + "0".repeat(64)
                + "\",\"profile\":{\"childProfileId\":\"123e4567-e89b-12d3-a456-426614174000\","
                + "\"displayName\":\"An\",\"birthYear\":" + birthYear
                + ",\"interests\":[],\"learningStyle\":null,\"vocabularyLevel\":null,\"parentCareer\":null}}";
    }
}
