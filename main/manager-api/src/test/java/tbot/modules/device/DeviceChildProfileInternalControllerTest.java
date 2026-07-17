package tbot.modules.device;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.List;

import org.junit.jupiter.api.Test;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestMapping;

import com.fasterxml.jackson.databind.ObjectMapper;

import tbot.modules.device.controller.DeviceChildProfileInternalController;
import tbot.modules.device.dto.DeviceChildProfileProjectionDTO;

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
}
