package tbot.modules.robot.projection;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Objects;
import java.util.regex.Pattern;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

public final class ChildProfileProjectionCanonicalizer {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private static final Pattern CANONICAL_UUID = Pattern.compile(
            "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$");

    private ChildProfileProjectionCanonicalizer() {
    }

    public record ChildProfileProjection(
            String childProfileId,
            String displayName,
            Integer birthYear,
            List<String> interests,
            String learningStyle,
            String vocabularyLevel,
            String parentCareer) {
        public ChildProfileProjection {
            interests = List.copyOf(interests);
        }
    }

    public record CanonicalProjectionEnvelope(String canonicalJson, String sha256) {
    }

    public static CanonicalProjectionEnvelope canonicalize(
            String mode,
            long revision,
            ChildProfileProjection profile) {
        if (!"replace".equals(mode) && !"clear".equals(mode)) {
            throw new IllegalArgumentException("mode must be replace or clear");
        }
        if ("replace".equals(mode) && profile == null) {
            throw new IllegalArgumentException("replace mode requires a profile");
        }
        if ("clear".equals(mode) && profile != null) {
            throw new IllegalArgumentException("clear mode requires a null profile");
        }

        String canonicalJson = "{\"mode\":" + jsonString(mode)
                + ",\"profile\":" + (profile == null ? "null" : canonicalProfile(profile))
                + ",\"revision\":" + revision + "}";
        return new CanonicalProjectionEnvelope(canonicalJson, sha256(canonicalJson));
    }

    private static String canonicalProfile(ChildProfileProjection profile) {
        if (!CANONICAL_UUID.matcher(profile.childProfileId()).matches()) {
            throw new IllegalArgumentException("childProfileId must be a canonical lowercase UUID");
        }

        List<String> interests = new ArrayList<>();
        for (String interest : profile.interests()) {
            interests.add(normalize(Objects.requireNonNull(interest, "interest")));
        }
        interests = new ArrayList<>(new LinkedHashSet<>(interests));
        interests.sort(ChildProfileProjectionCanonicalizer::compareByCodePoint);

        return "{\"birthYear\":" + nullableInteger(profile.birthYear())
                + ",\"childProfileId\":" + jsonString(profile.childProfileId())
                + ",\"displayName\":" + jsonString(normalize(profile.displayName()))
                + ",\"interests\":" + jsonStringArray(interests)
                + ",\"learningStyle\":" + nullableString(profile.learningStyle())
                + ",\"parentCareer\":" + nullableString(profile.parentCareer())
                + ",\"vocabularyLevel\":" + nullableString(profile.vocabularyLevel()) + "}";
    }

    private static String normalize(String value) {
        return Normalizer.normalize(Objects.requireNonNull(value), Normalizer.Form.NFC);
    }

    private static int compareByCodePoint(String left, String right) {
        int leftIndex = 0;
        int rightIndex = 0;
        while (leftIndex < left.length() && rightIndex < right.length()) {
            int leftCodePoint = left.codePointAt(leftIndex);
            int rightCodePoint = right.codePointAt(rightIndex);
            if (leftCodePoint != rightCodePoint) {
                return Integer.compare(leftCodePoint, rightCodePoint);
            }
            leftIndex += Character.charCount(leftCodePoint);
            rightIndex += Character.charCount(rightCodePoint);
        }
        return Integer.compare(left.length() - leftIndex, right.length() - rightIndex);
    }

    private static String jsonStringArray(List<String> values) {
        return values.stream().map(ChildProfileProjectionCanonicalizer::jsonString)
                .reduce((left, right) -> left + "," + right)
                .map(value -> "[" + value + "]")
                .orElse("[]");
    }

    private static String nullableString(String value) {
        return value == null ? "null" : jsonString(normalize(value));
    }

    private static String nullableInteger(Integer value) {
        return value == null ? "null" : value.toString();
    }

    private static String jsonString(String value) {
        try {
            return OBJECT_MAPPER.writeValueAsString(value);
        } catch (JsonProcessingException exception) {
            throw new IllegalArgumentException("unable to encode canonical JSON string", exception);
        }
    }

    private static String sha256(String canonicalJson) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(canonicalJson.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }
}
