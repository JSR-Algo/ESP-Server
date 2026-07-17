package tbot.modules.device;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.List;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import tbot.modules.device.dao.DeviceDao;
import tbot.modules.device.dto.DeviceChildProfileProjectionDTO;
import tbot.modules.device.dto.DeviceChildProfileProjectionDTO.Profile;
import tbot.modules.device.entity.DeviceEntity;
import tbot.modules.device.service.DeviceChildProfileProjectionService;
import tbot.modules.device.service.DeviceChildProfileProjectionService.ProjectionResult;
import tbot.modules.device.service.DeviceChildProfileProjectionService.ProjectionConflictException;
import tbot.modules.robot.projection.ChildProfileProjectionCanonicalizer;

class DeviceChildProfileProjectionServiceTest {
    private static final String DEVICE_ID = "device-123";
    private static final String PROFILE_ID = "123e4567-e89b-12d3-a456-426614174000";

    @Mock
    private DeviceDao deviceDao;

    private DeviceChildProfileProjectionService service;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        service = new DeviceChildProfileProjectionService(deviceDao);
    }

    @Test
    void rejectsNonCanonicalProfileUuid() {
        DeviceChildProfileProjectionDTO request = new DeviceChildProfileProjectionDTO(
                "replace", 1, "0".repeat(64), profile("123E4567-E89B-12D3-A456-426614174000"));
        assertThrows(IllegalArgumentException.class, () -> service.apply(DEVICE_ID, request));
        verify(deviceDao, never()).selectChildProfileForUpdate(any());
    }

    @Test
    void fullyReplacesProfileAndReturnsThePersistedCanonicalProjection() {
        DeviceEntity persisted = storedProfile(7, "unused", PROFILE_ID, "An", 2018,
                "[\"music\",\"robots\"]", "visual", "beginner", "engineer");
        Profile profile = profile(PROFILE_ID);
        DeviceChildProfileProjectionDTO request = replace(7, profile);
        persisted.setChildProfilePayloadHash(request.getPayloadHash());
        when(deviceDao.selectChildProfileForUpdate(DEVICE_ID)).thenReturn(stored(-1, null), persisted);

        ProjectionResult result = service.apply(DEVICE_ID, request);

        assertEquals(DeviceChildProfileProjectionService.Outcome.APPLIED, result.outcome());
        assertEquals(DEVICE_ID, result.deviceId());
        assertEquals(7, result.revision());
        assertEquals(request.getPayloadHash(), result.payloadHash());
        assertEquals("replace", result.mode());
        assertEquals(PROFILE_ID, result.profile().childProfileId());
        assertEquals("An", result.profile().displayName());
        assertEquals(2018, result.profile().birthYear());
        assertEquals(List.of("music", "robots"), result.profile().interests());
        assertEquals("visual", result.profile().learningStyle());
        assertEquals("beginner", result.profile().vocabularyLevel());
        assertEquals("engineer", result.profile().parentCareer());

        ArgumentCaptor<DeviceEntity> captor = ArgumentCaptor.forClass(DeviceEntity.class);
        verify(deviceDao).replaceChildProfile(captor.capture());
        DeviceEntity updated = captor.getValue();
        assertEquals(DEVICE_ID, updated.getId());
        assertEquals(PROFILE_ID, updated.getChildProfileId());
        assertEquals(2018, updated.getChildBirthYear());
        assertEquals("An", updated.getChildName());
        assertNull(updated.getChildAge());
        assertNull(updated.getChildInterests());
        assertEquals("[\"music\",\"robots\"]", updated.getChildInterestsJson());
        assertEquals("visual", updated.getLearningStyle());
        assertEquals("beginner", updated.getVocabularyLevel());
        assertEquals("engineer", updated.getParentCareer());
        assertEquals(7, updated.getChildProfileRevision());
        assertEquals(request.getPayloadHash(), updated.getChildProfilePayloadHash());
    }

    @Test
    void fullClearReturnsPersistedNullProjection() {
        DeviceChildProfileProjectionDTO request = clear(5);
        DeviceEntity persisted = stored(5, request.getPayloadHash());
        when(deviceDao.selectChildProfileForUpdate(DEVICE_ID))
                .thenReturn(storedProfile(4, "0".repeat(64), PROFILE_ID, "Stale", 2018,
                        "music", "visual", "beginner", "engineer"), persisted);

        ProjectionResult result = service.apply(DEVICE_ID, request);

        assertEquals(DeviceChildProfileProjectionService.Outcome.APPLIED, result.outcome());
        assertEquals("clear", result.mode());
        assertNull(result.profile());

        ArgumentCaptor<DeviceEntity> captor = ArgumentCaptor.forClass(DeviceEntity.class);
        verify(deviceDao).clearChildProfile(captor.capture());
        DeviceEntity cleared = captor.getValue();
        assertEquals(DEVICE_ID, cleared.getId());
        assertEquals(5, cleared.getChildProfileRevision());
        assertEquals(request.getPayloadHash(), cleared.getChildProfilePayloadHash());
    }

    @Test
    void rejectsLowerRevision() {
        when(deviceDao.selectChildProfileForUpdate(DEVICE_ID)).thenReturn(stored(9, "0".repeat(64)));
        assertThrows(ProjectionConflictException.class, () -> service.apply(DEVICE_ID, clear(8)));
        verify(deviceDao, never()).clearChildProfile(any());
    }

    @Test
    void sameRevisionAndHashIsIdempotent() {
        DeviceChildProfileProjectionDTO request = clear(9);
        when(deviceDao.selectChildProfileForUpdate(DEVICE_ID)).thenReturn(stored(9, request.getPayloadHash()));
        ProjectionResult result = service.apply(DEVICE_ID, request);
        assertEquals(DeviceChildProfileProjectionService.Outcome.NO_OP, result.outcome());
        assertEquals("clear", result.mode());
        assertNull(result.profile());
        verify(deviceDao, never()).clearChildProfile(any());
    }

    @Test
    void refusesToAttestAStoredClearProjectionWithResidualProfileMaterial() {
        DeviceChildProfileProjectionDTO request = clear(9);
        DeviceEntity partialClear = stored(9, request.getPayloadHash());
        partialClear.setChildName("Residual child name");
        partialClear.setChildAge(8);
        when(deviceDao.selectChildProfileForUpdate(DEVICE_ID)).thenReturn(partialClear);

        assertThrows(IllegalStateException.class, () -> service.apply(DEVICE_ID, request));
        verify(deviceDao, never()).clearChildProfile(any());
    }

    @Test
    void sameRevisionReplayRejectsStoredFieldsThatDoNotMatchTheCanonicalEnvelope() {
        Profile requested = profile(PROFILE_ID);
        DeviceChildProfileProjectionDTO request = replace(9, requested);
        DeviceEntity corrupted = storedProfile(9, request.getPayloadHash(), PROFILE_ID,
                "Database value", 2017, "art", null, "advanced", null);
        corrupted.setChildAge(99);
        when(deviceDao.selectChildProfileForUpdate(DEVICE_ID)).thenReturn(corrupted);

        assertThrows(IllegalStateException.class, () -> service.apply(DEVICE_ID, request));
        verify(deviceDao, never()).replaceChildProfile(any());
    }

    @Test
    void sameRevisionReplayRejectsResidualLegacyAgeAndMissingAuthoritativeJson() {
        DeviceChildProfileProjectionDTO request = replace(9, profile(PROFILE_ID));
        DeviceEntity incoherent = storedProfile(9, request.getPayloadHash(), PROFILE_ID,
                "An", 2018, "music,robots", "visual", "beginner", "engineer");
        incoherent.setChildAge(8);
        incoherent.setChildInterestsJson(null);
        when(deviceDao.selectChildProfileForUpdate(DEVICE_ID)).thenReturn(incoherent);

        assertThrows(IllegalStateException.class, () -> service.apply(DEVICE_ID, request));
        verify(deviceDao, never()).replaceChildProfile(any());
    }

    @Test
    void sameRevisionAndDifferentHashConflicts() {
        when(deviceDao.selectChildProfileForUpdate(DEVICE_ID)).thenReturn(stored(9, "0".repeat(64)));
        assertThrows(ProjectionConflictException.class, () -> service.apply(DEVICE_ID, clear(9)));
        verify(deviceDao, never()).clearChildProfile(any());
    }

    @Test
    void persistsTheNormalizedProfileRepresentedByCanonicalBytes() {
        when(deviceDao.selectChildProfileForUpdate(DEVICE_ID)).thenReturn(stored(-1, null));
        Profile profile = new Profile(PROFILE_ID, "A\u0301n", 2018,
                List.of("é", "z", "e\u0301", "a"), "cafe\u0301", "de\u0301butant", "inge\u0301nieur");
        DeviceChildProfileProjectionDTO request = replace(3, profile);

        DeviceEntity persisted = storedProfile(3, request.getPayloadHash(), PROFILE_ID, "Án", 2018,
                "[\"a\",\"z\",\"é\"]", "café", "débutant", "ingénieur");
        when(deviceDao.selectChildProfileForUpdate(DEVICE_ID)).thenReturn(stored(-1, null), persisted);

        service.apply(DEVICE_ID, request);

        ArgumentCaptor<DeviceEntity> captor = ArgumentCaptor.forClass(DeviceEntity.class);
        verify(deviceDao).replaceChildProfile(captor.capture());
        DeviceEntity stored = captor.getValue();
        assertEquals("Án", stored.getChildName());
        assertNull(stored.getChildInterests());
        assertEquals("[\"a\",\"z\",\"é\"]", stored.getChildInterestsJson());
        assertEquals("café", stored.getLearningStyle());
        assertEquals("débutant", stored.getVocabularyLevel());
        assertEquals("ingénieur", stored.getParentCareer());
    }

    @Test
    void losslesslyEncodesCommaAndEmptyInterestsAndReturnsTheStoredJsonProjection() {
        Profile profile = new Profile(PROFILE_ID, "An", 2018,
                List.of("science, technology", ""), null, null, null);
        DeviceChildProfileProjectionDTO request = replace(4, profile);
        DeviceEntity persisted = storedProfile(4, request.getPayloadHash(), PROFILE_ID, "An", 2018,
                "[\"\",\"science, technology\"]", null, null, null);
        when(deviceDao.selectChildProfileForUpdate(DEVICE_ID)).thenReturn(stored(-1, null), persisted);

        ProjectionResult result = service.apply(DEVICE_ID, request);

        ArgumentCaptor<DeviceEntity> captor = ArgumentCaptor.forClass(DeviceEntity.class);
        verify(deviceDao).replaceChildProfile(captor.capture());
        assertNull(captor.getValue().getChildInterests());
        assertEquals("[\"\",\"science, technology\"]", captor.getValue().getChildInterestsJson());
        assertEquals(List.of("", "science, technology"), result.profile().interests());
    }

    @Test
    void acceptsLosslessInterestJsonBeyondTheLegacyVarcharLimit() {
        String longInterest = "robotics," + "x".repeat(400);
        Profile profile = new Profile(PROFILE_ID, "An", 2018,
                List.of("", longInterest), null, null, null);
        DeviceChildProfileProjectionDTO request = replace(4, profile);
        DeviceEntity persisted = storedProfile(4, request.getPayloadHash(), PROFILE_ID, "An", 2018,
                null, null, null, null);
        persisted.setChildInterestsJson("[\"\",\"" + longInterest + "\"]");
        when(deviceDao.selectChildProfileForUpdate(DEVICE_ID)).thenReturn(stored(-1, null), persisted);

        ProjectionResult result = service.apply(DEVICE_ID, request);

        ArgumentCaptor<DeviceEntity> captor = ArgumentCaptor.forClass(DeviceEntity.class);
        verify(deviceDao).replaceChildProfile(captor.capture());
        assertNull(captor.getValue().getChildInterests());
        assertEquals("[\"\",\"" + longInterest + "\"]", captor.getValue().getChildInterestsJson());
        assertEquals(List.of("", longInterest), result.profile().interests());
    }

    @ParameterizedTest(name = "rejects profile storage overflow: {0}")
    @MethodSource("oversizedProfiles")
    void rejectsProfileFieldsThatExceedStorageBeforeLocking(String field, Profile profile) {
        DeviceChildProfileProjectionDTO request = replace(1, profile);
        assertThrows(IllegalArgumentException.class, () -> service.apply(DEVICE_ID, request));
        verify(deviceDao, never()).selectChildProfileForUpdate(any());
    }

    private static List<org.junit.jupiter.params.provider.Arguments> oversizedProfiles() {
        return List.of(
                org.junit.jupiter.params.provider.Arguments.of("displayName", new Profile(PROFILE_ID, "x".repeat(65), 2018, List.of(), null, null, null)),
                org.junit.jupiter.params.provider.Arguments.of("interest count", new Profile(PROFILE_ID, "An", 2018,
                        java.util.stream.IntStream.range(0, 257).mapToObj(index -> "interest-" + index).toList(), null, null, null)),
                org.junit.jupiter.params.provider.Arguments.of("interest length", new Profile(PROFILE_ID, "An", 2018,
                        List.of("x".repeat(4097)), null, null, null)),
                org.junit.jupiter.params.provider.Arguments.of("learningStyle", new Profile(PROFILE_ID, "An", 2018, List.of(), "x".repeat(33), null, null)),
                org.junit.jupiter.params.provider.Arguments.of("vocabularyLevel", new Profile(PROFILE_ID, "An", 2018, List.of(), null, "x".repeat(33), null)),
                org.junit.jupiter.params.provider.Arguments.of("parentCareer", new Profile(PROFILE_ID, "An", 2018, List.of(), null, null, "x".repeat(65))));
    }

    private static DeviceChildProfileProjectionDTO replace(long revision, Profile profile) {
        String hash = ChildProfileProjectionCanonicalizer.canonicalize("replace", revision, profile.toCanonicalProfile()).sha256();
        return new DeviceChildProfileProjectionDTO("replace", revision, hash, profile);
    }

    private static DeviceChildProfileProjectionDTO clear(long revision) {
        String hash = ChildProfileProjectionCanonicalizer.canonicalize("clear", revision, null).sha256();
        return new DeviceChildProfileProjectionDTO("clear", revision, hash, null);
    }

    private static Profile profile(String id) {
        return new Profile(id, "An", 2018, List.of("music", "robots"), "visual", "beginner", "engineer");
    }

    private static DeviceEntity stored(long revision, String hash) {
        DeviceEntity device = new DeviceEntity();
        device.setId(DEVICE_ID);
        device.setChildProfileRevision(revision);
        device.setChildProfilePayloadHash(hash);
        return device;
    }

    private static DeviceEntity storedProfile(long revision, String hash, String profileId,
            String name, Integer birthYear, String interests, String style, String vocabulary,
            String career) {
        DeviceEntity device = stored(revision, hash);
        device.setChildProfileId(profileId);
        device.setChildName(name);
        device.setChildBirthYear(birthYear);
        device.setChildInterestsJson(interests != null && interests.stripLeading().startsWith("[") ? interests : null);
        device.setChildInterests(interests != null && interests.stripLeading().startsWith("[") ? null : interests);
        device.setLearningStyle(style);
        device.setVocabularyLevel(vocabulary);
        device.setParentCareer(career);
        return device;
    }
}
