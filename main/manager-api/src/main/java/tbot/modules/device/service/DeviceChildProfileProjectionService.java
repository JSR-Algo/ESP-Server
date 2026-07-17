package tbot.modules.device.service;

import java.time.Year;
import java.util.Objects;

import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.ResponseStatus;

import tbot.modules.device.dao.DeviceDao;
import tbot.modules.device.dto.DeviceChildProfileProjectionDTO;
import tbot.modules.device.dto.DeviceChildProfileProjectionDTO.Profile;
import tbot.modules.device.entity.DeviceEntity;
import tbot.modules.robot.projection.ChildProfileProjectionCanonicalizer;

@Service
public class DeviceChildProfileProjectionService {
    private final DeviceDao deviceDao;

    public DeviceChildProfileProjectionService(DeviceDao deviceDao) {
        this.deviceDao = deviceDao;
    }

    @Transactional(rollbackFor = Exception.class)
    public Outcome apply(String deviceId, DeviceChildProfileProjectionDTO request) {
        validateRequest(request);
        var canonical = ChildProfileProjectionCanonicalizer.canonicalize(
                request.getMode(), request.getRevision(),
                request.getProfile() == null ? null : request.getProfile().toCanonicalProfile());
        if (!canonical.sha256().equals(request.getPayloadHash())) {
            throw new IllegalArgumentException("payloadHash does not match canonical projection");
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
                return Outcome.NO_OP;
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
            Profile profile = request.getProfile();
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
        return Outcome.APPLIED;
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
        if (request.getProfile() != null) {
            validateProfileStorage(request.getProfile());
        }
    }

    private static void validateProfileStorage(Profile profile) {
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

    @ResponseStatus(HttpStatus.CONFLICT)
    public static class ProjectionConflictException extends RuntimeException {
        public ProjectionConflictException(String message) { super(message); }
    }

    @ResponseStatus(HttpStatus.NOT_FOUND)
    public static class DeviceNotFoundException extends RuntimeException {
        public DeviceNotFoundException() { super("device does not exist"); }
    }
}
