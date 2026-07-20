package tbot.modules.device;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.List;
import java.util.function.Consumer;

import org.apache.shiro.subject.Subject;
import org.apache.shiro.util.ThreadContext;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import tbot.common.redis.RedisUtils;
import tbot.common.user.UserDetail;
import tbot.modules.device.controller.DeviceController;
import tbot.modules.device.dto.DeviceUpdateDTO;
import tbot.modules.device.entity.DeviceEntity;
import tbot.modules.device.service.DeviceService;
import tbot.modules.sys.service.SysParamsService;

class DeviceControllerChildProfileAuthorityTest {
    private static final String DEVICE_ID = "device-1";

    private DeviceService deviceService;
    private DeviceEntity stored;
    private DeviceController controller;

    @BeforeEach
    void setUp() {
        deviceService = mock(DeviceService.class);
        stored = new DeviceEntity();
        stored.setId(DEVICE_ID);
        stored.setUserId(7L);
        when(deviceService.selectById(DEVICE_ID)).thenReturn(stored);
        controller = new DeviceController(deviceService, mock(RedisUtils.class), mock(SysParamsService.class));

        UserDetail principal = new UserDetail();
        principal.setId(7L);
        Subject subject = mock(Subject.class);
        when(subject.getPrincipal()).thenReturn(principal);
        ThreadContext.bind(subject);
    }

    @AfterEach
    void tearDown() {
        ThreadContext.unbindSubject();
    }

    @ParameterizedTest(name = "projected profile rejects legacy mutation: {0}")
    @MethodSource("legacyProfileMutations")
    void rejectsEveryLegacyChildProfileMutationAfterProjectionAuthority(
            String field, Consumer<DeviceUpdateDTO> mutation) {
        stored.setChildProfileRevision(0L);
        DeviceUpdateDTO dto = new DeviceUpdateDTO();
        mutation.accept(dto);

        RuntimeException error = assertThrows(RuntimeException.class,
                () -> controller.updateDeviceInfo(DEVICE_ID, dto));

        assertEquals("projected_child_profile_managed", error.getMessage());
        verify(deviceService, never()).updateDeviceInfo(any());
    }

    @ParameterizedTest(name = "legacy profile remains editable: {0}")
    @MethodSource("legacyProfileMutations")
    void preservesLegacyProfileEditingBeforeProjectionAuthority(
            String field, Consumer<DeviceUpdateDTO> mutation) {
        stored.setChildProfileRevision(-1L);
        DeviceUpdateDTO dto = new DeviceUpdateDTO();
        mutation.accept(dto);

        assertDoesNotThrow(() -> controller.updateDeviceInfo(DEVICE_ID, dto));
        verify(deviceService).updateDeviceInfo(any());
    }

    @ParameterizedTest(name = "canonical profile field remains internal-only: {0}")
    @MethodSource("canonicalProfileMutations")
    void rejectsCanonicalProjectionFieldsEvenOnLegacyRows(
            String field, Consumer<DeviceUpdateDTO> mutation) {
        stored.setChildProfileRevision(-1L);
        DeviceUpdateDTO dto = new DeviceUpdateDTO();
        mutation.accept(dto);

        RuntimeException error = assertThrows(RuntimeException.class,
                () -> controller.updateDeviceInfo(DEVICE_ID, dto));

        assertEquals("projected_child_profile_managed", error.getMessage());
        verify(deviceService, never()).updateDeviceInfo(any());
    }

    @Test
    void projectedProfileMutationReturnsHttpConflict() throws Exception {
        stored.setChildProfileRevision(0L);

        MockMvcBuilders.standaloneSetup(controller).build()
                .perform(put("/device/update/{id}", DEVICE_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"childName\":\"An\"}"))
                .andExpect(status().isConflict());

        verify(deviceService, never()).updateDeviceInfo(any());
    }

    private static List<Arguments> legacyProfileMutations() {
        return List.of(
                Arguments.of("childName", (Consumer<DeviceUpdateDTO>) dto -> dto.setChildName("An")),
                Arguments.of("childAge", (Consumer<DeviceUpdateDTO>) dto -> dto.setChildAge(8)),
                Arguments.of("childInterests", (Consumer<DeviceUpdateDTO>) dto -> dto.setChildInterests("robots")),
                Arguments.of("learningStyle", (Consumer<DeviceUpdateDTO>) dto -> dto.setLearningStyle("visual")),
                Arguments.of("vocabularyLevel", (Consumer<DeviceUpdateDTO>) dto -> dto.setVocabularyLevel("starter")),
                Arguments.of("parentCareer", (Consumer<DeviceUpdateDTO>) dto -> dto.setParentCareer("teacher")));
    }

    private static List<Arguments> canonicalProfileMutations() {
        return List.of(
                Arguments.of("childProfileId", (Consumer<DeviceUpdateDTO>) dto -> dto.setChildProfileId("id")),
                Arguments.of("childBirthYear", (Consumer<DeviceUpdateDTO>) dto -> dto.setChildBirthYear(2018)),
                Arguments.of("childProfileRevision", (Consumer<DeviceUpdateDTO>) dto -> dto.setChildProfileRevision(3L)),
                Arguments.of("childProfilePayloadHash", (Consumer<DeviceUpdateDTO>) dto -> dto.setChildProfilePayloadHash("0".repeat(64))),
                Arguments.of("childInterestsJson", (Consumer<DeviceUpdateDTO>) dto -> dto.setChildInterestsJson("[]")));
    }
}
