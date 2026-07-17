package tbot.modules.device.service;

import java.time.Year;
import java.util.Arrays;
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
        if (canonical.normalizedProfile() != null) {
            validateProfileStorage(canonical.normalizedProfile());
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
            update.setChildAge(legacyAge(profile.birthYear()));
            update.setChildInterests(String.join(",", profile.interests()));
            update.setLearningStyle(profile.learningStyle());
            update.setVocabularyLevel(profile.vocabularyLevel());
            update.setParentCareer(profile.parentCareer());
            deviceDao.replaceChildProfile(update);
        }
        DeviceEntity persisted = deviceDao.selectChildProfileForUpdate(deviceId);
        if (persisted == null) {
            throw new IllegalStateException("device disappeared after child profile projection");
        }
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
                        storedInterests(stored.getChildInterests()),
                        stored.getLearningStyle(),
                        stored.getVocabularyLevel(),
                        stored.getParentCareer()));
    }

    private static boolean isFullyCleared(DeviceEntity stored) {
        return stored.getChildBirthYear() == null
                && stored.getChildName() == null
                && stored.getChildAge() == null
                && stored.getChildInterests() == null
                && stored.getLearningStyle() == null
                && stored.getVocabularyLevel() == null
                && stored.getParentCareer() == null;
    }

    private static List<String> storedInterests(String interests) {
        if (interests == null || interests.isEmpty()) {
            return List.of();
        }
        return List.copyOf(Arrays.asList(interests.split(",", -1)));
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

    private static void validateProfileStorage(ChildProfileProjection profile) {
        if (profile.displayName() == null || profile.interests() == null) {
            throw new IllegalArgumentException("required profile fields are missing");
        }
        if (profile.interests().stream().anyMatch(Objects::isNull)) {
            throw new IllegalArgumentException("interests cannot contain null values");
        }
        requireMaxLength("displayName", profile.displayName(), 64);
        requireMaxLength("interests", String.join(",", profile.interests()), 255);
        requireMaxLength("learningStyle", profile.learningStyle(), 32);
        requireMaxLength("vocabularyLevel", profile.vocabularyLevel(), 32);
        requireMaxLength("parentCareer", profile.parentCareer(), 64);
    }

    private static void requireMaxLength(String field, String value, int maximum) {
        if (value != null && value.codePointCount(0, value.length()) > maximum) {
            throw new IllegalArgumentException(field + " exceeds storage capacity");
        }
    }

    private static Integer legacyAge(Integer birthYear) {
        if (birthYear == null) {
            return null;
        }
        long age = (long) Year.now().getValue() - birthYear;
        return age >= 0 && age <= 255 ? (int) age : null;
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
