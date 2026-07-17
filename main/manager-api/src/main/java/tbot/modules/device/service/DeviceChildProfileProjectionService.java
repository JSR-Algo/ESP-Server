package tbot.modules.device.service;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Objects;

import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.ResponseStatus;

import tbot.modules.device.dao.DeviceDao;
import tbot.modules.device.dto.DeviceChildProfileProjectionDTO;
import tbot.modules.device.entity.DeviceEntity;
import tbot.modules.robot.projection.ChildProfileProjectionCanonicalizer;
import tbot.modules.robot.projection.ChildProfileProjectionCanonicalizer.ChildProfileProjection;

@Service
public class DeviceChildProfileProjectionService {
    private final DeviceDao deviceDao;

    public DeviceChildProfileProjectionService(DeviceDao deviceDao) {
        this.deviceDao = deviceDao;
    }

    @Transactional(rollbackFor = Exception.class)
    public ProjectionResult apply(String deviceId, DeviceChildProfileProjectionDTO request) {
        validateRequest(request);
        var canonical = ChildProfileProjectionCanonicalizer.canonicalize(
                request.getMode(), request.getRevision(),
                request.getProfile() == null ? null : request.getProfile().toCanonicalProfile());
        if (!canonical.sha256().equals(request.getPayloadHash())) {
            throw new IllegalArgumentException("payloadHash does not match canonical projection");
        }
        String encodedInterests = null;
        if (canonical.normalizedProfile() != null) {
            encodedInterests = ChildInterestsCodec.encode(
                    canonical.normalizedProfile().interests());
            validateProfileStorage(canonical.normalizedProfile(), encodedInterests);
        }

        DeviceEntity stored = deviceDao.selectChildProfileForUpdate(deviceId);
        if (stored == null) {
            throw new DeviceNotFoundException();
        }
        long storedRevision = stored.getChildProfileRevision() == null ? -1 : stored.getChildProfileRevision();
        long incomingRevision = request.getRevision();
        if (incomingRevision < storedRevision) {
            throw new ProjectionConflictException("child profile revision is stale");
        }
        if (incomingRevision == storedRevision) {
            if (Objects.equals(request.getPayloadHash(), stored.getChildProfilePayloadHash())) {
                assertStoredMatchesCanonical(stored, canonical.normalizedProfile(), encodedInterests);
                return result(Outcome.NO_OP, stored);
            }
            throw new ProjectionConflictException("child profile revision hash conflicts");
        }

        DeviceEntity update = new DeviceEntity();
        update.setId(deviceId);
        update.setChildProfileRevision(incomingRevision);
        update.setChildProfilePayloadHash(request.getPayloadHash());
        if ("clear".equals(request.getMode())) {
            deviceDao.clearChildProfile(update);
        } else {
            ChildProfileProjection profile = canonical.normalizedProfile();
            update.setChildProfileId(profile.childProfileId());
            update.setChildBirthYear(profile.birthYear());
            update.setChildName(profile.displayName());
            update.setChildAge(null);
            update.setChildInterests(null);
            update.setChildInterestsJson(encodedInterests);
            update.setLearningStyle(profile.learningStyle());
            update.setVocabularyLevel(profile.vocabularyLevel());
            update.setParentCareer(profile.parentCareer());
            deviceDao.replaceChildProfile(update);
        }
        DeviceEntity persisted = deviceDao.selectChildProfileForUpdate(deviceId);
        if (persisted == null) {
            throw new IllegalStateException("device disappeared after child profile projection");
        }
        assertStoredMatchesCanonical(persisted, canonical.normalizedProfile(), encodedInterests);
        return result(Outcome.APPLIED, persisted);
    }

    private static ProjectionResult result(Outcome outcome, DeviceEntity stored) {
        long revision = stored.getChildProfileRevision() == null ? -1 : stored.getChildProfileRevision();
        if (stored.getChildProfileId() == null) {
            if (!isFullyCleared(stored)) {
                throw new IllegalStateException("stored child profile clear projection is incoherent");
            }
            return new ProjectionResult(outcome, stored.getId(), revision,
                    stored.getChildProfilePayloadHash(), "clear", null);
        }
        return new ProjectionResult(outcome, stored.getId(), revision,
                stored.getChildProfilePayloadHash(), "replace", new StoredProfile(
                        stored.getChildProfileId(),
                        stored.getChildName(),
                        stored.getChildBirthYear(),
                        storedInterests(stored),
                        stored.getLearningStyle(),
                        stored.getVocabularyLevel(),
                        stored.getParentCareer()));
    }

    private static boolean isFullyCleared(DeviceEntity stored) {
        return stored.getChildBirthYear() == null
                && stored.getChildName() == null
                && stored.getChildAge() == null
                && stored.getChildInterests() == null
                && stored.getChildInterestsJson() == null
                && stored.getLearningStyle() == null
                && stored.getVocabularyLevel() == null
                && stored.getParentCareer() == null;
    }

    private static List<String> storedInterests(DeviceEntity stored) {
        String interests = stored.getChildInterestsJson() != null
                ? stored.getChildInterestsJson()
                : stored.getChildInterests();
        if (interests == null || interests.isEmpty()) {
            return List.of();
        }
        return ChildInterestsCodec.decodeJsonOrLegacy(interests);
    }

    private static void validateRequest(DeviceChildProfileProjectionDTO request) {
        if (request == null || request.getMode() == null || request.getRevision() == null
                || request.getPayloadHash() == null) {
            throw new IllegalArgumentException("projection fields are required");
        }
        if (!request.getPayloadHash().matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException("payloadHash must be lowercase SHA-256 hex");
        }
        if (("replace".equals(request.getMode())) != (request.getProfile() != null)) {
            throw new IllegalArgumentException("mode/profile coherence violation");
        }
    }

    private static void validateProfileStorage(
            ChildProfileProjection profile,
            String encodedInterests) {
        if (profile.displayName() == null || profile.interests() == null) {
            throw new IllegalArgumentException("required profile fields are missing");
        }
        if (profile.interests().stream().anyMatch(Objects::isNull)) {
            throw new IllegalArgumentException("interests cannot contain null values");
        }
        requireMaxLength("displayName", profile.displayName(), 64);
        if (profile.interests().size() > 256) {
            throw new IllegalArgumentException("interests exceeds item capacity");
        }
        profile.interests().forEach(interest -> requireMaxLength("interest", interest, 4_096));
        requireMaxUtf8Bytes("interests", encodedInterests, 65_535);
        requireMaxLength("learningStyle", profile.learningStyle(), 32);
        requireMaxLength("vocabularyLevel", profile.vocabularyLevel(), 32);
        requireMaxLength("parentCareer", profile.parentCareer(), 64);
    }

    private static void requireMaxLength(String field, String value, int maximum) {
        if (value != null && value.codePointCount(0, value.length()) > maximum) {
            throw new IllegalArgumentException(field + " exceeds storage capacity");
        }
    }

    private static void requireMaxUtf8Bytes(String field, String value, int maximum) {
        if (value != null && value.getBytes(StandardCharsets.UTF_8).length > maximum) {
            throw new IllegalArgumentException(field + " exceeds storage capacity");
        }
    }

    private static void assertStoredMatchesCanonical(
            DeviceEntity stored,
            ChildProfileProjection expected,
            String encodedInterests) {
        if (expected == null) {
            if (!isFullyCleared(stored)) {
                throw new IllegalStateException("stored child profile clear projection is incoherent");
            }
            return;
        }
        if (!Objects.equals(stored.getChildProfileId(), expected.childProfileId())
                || !Objects.equals(stored.getChildBirthYear(), expected.birthYear())
                || !Objects.equals(stored.getChildName(), expected.displayName())
                || stored.getChildAge() != null
                || stored.getChildInterests() != null
                || !Objects.equals(stored.getChildInterestsJson(), encodedInterests)
                || !Objects.equals(stored.getLearningStyle(), expected.learningStyle())
                || !Objects.equals(stored.getVocabularyLevel(), expected.vocabularyLevel())
                || !Objects.equals(stored.getParentCareer(), expected.parentCareer())) {
            throw new IllegalStateException("stored child profile replace projection is incoherent");
        }
    }

    public enum Outcome { APPLIED, NO_OP }

    public record StoredProfile(
            String childProfileId,
            String displayName,
            Integer birthYear,
            List<String> interests,
            String learningStyle,
            String vocabularyLevel,
            String parentCareer) {}

    public record ProjectionResult(
            Outcome outcome,
            String deviceId,
            long revision,
            String payloadHash,
            String mode,
            StoredProfile profile) {}

    @ResponseStatus(HttpStatus.CONFLICT)
    public static class ProjectionConflictException extends RuntimeException {
        public ProjectionConflictException(String message) { super(message); }
    }

    @ResponseStatus(HttpStatus.NOT_FOUND)
    public static class DeviceNotFoundException extends RuntimeException {
        public DeviceNotFoundException() { super("device does not exist"); }
    }
}
