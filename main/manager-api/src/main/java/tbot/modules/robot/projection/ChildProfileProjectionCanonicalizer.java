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

public final class ChildProfileProjectionCanonicalizer {
    private static final long JAVASCRIPT_MAX_SAFE_INTEGER = 9007199254740991L;
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

    public record CanonicalProjectionEnvelope(
            String canonicalJson,
            String sha256,
            ChildProfileProjection normalizedProfile) {
    }

    public static CanonicalProjectionEnvelope canonicalize(
            String mode,
            long revision,
            ChildProfileProjection profile) {
        if (revision < 0 || revision > JAVASCRIPT_MAX_SAFE_INTEGER) {
            throw new IllegalArgumentException("revision must be a nonnegative JavaScript safe integer");
        }
        if (!"replace".equals(mode) && !"clear".equals(mode)) {
            throw new IllegalArgumentException("mode must be replace or clear");
        }
        if ("replace".equals(mode) && profile == null) {
            throw new IllegalArgumentException("replace mode requires a profile");
        }
        if ("clear".equals(mode) && profile != null) {
            throw new IllegalArgumentException("clear mode requires a null profile");
        }

        ChildProfileProjection normalizedProfile = profile == null ? null : normalizeProfile(profile);
        String canonicalJson = "{\"mode\":" + jsonString(mode)
                + ",\"profile\":" + (normalizedProfile == null ? "null" : canonicalProfile(normalizedProfile))
                + ",\"revision\":" + revision + "}";
        return new CanonicalProjectionEnvelope(canonicalJson, sha256(canonicalJson), normalizedProfile);
    }

    private static ChildProfileProjection normalizeProfile(ChildProfileProjection profile) {
        if (!CANONICAL_UUID.matcher(profile.childProfileId()).matches()) {
            throw new IllegalArgumentException("childProfileId must be a canonical lowercase UUID");
        }

        List<String> interests = new ArrayList<>();
        for (String interest : profile.interests()) {
            interests.add(normalize(Objects.requireNonNull(interest, "interest")));
        }
        interests = new ArrayList<>(new LinkedHashSet<>(interests));
        interests.sort(ChildProfileProjectionCanonicalizer::compareByCodePoint);

        return new ChildProfileProjection(
                profile.childProfileId(),
                normalize(profile.displayName()),
                profile.birthYear(),
                interests,
                normalizeNullable(profile.learningStyle()),
                normalizeNullable(profile.vocabularyLevel()),
                normalizeNullable(profile.parentCareer()));
    }

    private static String canonicalProfile(ChildProfileProjection profile) {
        return "{\"birthYear\":" + nullableInteger(profile.birthYear())
                + ",\"childProfileId\":" + jsonString(profile.childProfileId())
                + ",\"displayName\":" + jsonString(profile.displayName())
                + ",\"interests\":" + jsonStringArray(profile.interests())
                + ",\"learningStyle\":" + nullableString(profile.learningStyle())
                + ",\"parentCareer\":" + nullableString(profile.parentCareer())
                + ",\"vocabularyLevel\":" + nullableString(profile.vocabularyLevel()) + "}";
    }

    private static String normalize(String value) {
        String requiredValue = Objects.requireNonNull(value);
        assertUnicodeScalars(requiredValue);
        return Normalizer.normalize(requiredValue, Normalizer.Form.NFC);
    }

    private static void assertUnicodeScalars(String value) {
        for (int index = 0; index < value.length(); index++) {
            char codeUnit = value.charAt(index);
            if (Character.isHighSurrogate(codeUnit)) {
                if (index + 1 >= value.length() || !Character.isLowSurrogate(value.charAt(index + 1))) {
                    throw new IllegalArgumentException("profile strings must contain only Unicode scalar values");
                }
                index++;
            } else if (Character.isLowSurrogate(codeUnit)) {
                throw new IllegalArgumentException("profile strings must contain only Unicode scalar values");
            }
        }
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
        StringBuilder encoded = new StringBuilder("[");
        for (int index = 0; index < values.size(); index++) {
            if (index > 0) {
                encoded.append(',');
            }
            encoded.append(jsonString(values.get(index)));
        }
        return encoded.append(']').toString();
    }

    private static String nullableString(String value) {
        return value == null ? "null" : jsonString(value);
    }

    private static String normalizeNullable(String value) {
        return value == null ? null : normalize(value);
    }

    private static String nullableInteger(Integer value) {
        return value == null ? "null" : value.toString();
    }

    private static String jsonString(String value) {
        assertUnicodeScalars(value);
        StringBuilder encoded = new StringBuilder(value.length() + 2).append('"');
        for (int index = 0; index < value.length(); index++) {
            char codeUnit = value.charAt(index);
            switch (codeUnit) {
                case '"' -> encoded.append("\\\"");
                case '\\' -> encoded.append("\\\\");
                case '\b' -> encoded.append("\\b");
                case '\t' -> encoded.append("\\t");
                case '\n' -> encoded.append("\\n");
                case '\f' -> encoded.append("\\f");
                case '\r' -> encoded.append("\\r");
                default -> {
                    if (codeUnit <= 0x1f) {
                        encoded.append("\\u00")
                                .append(Character.forDigit((codeUnit >>> 4) & 0xf, 16))
                                .append(Character.forDigit(codeUnit & 0xf, 16));
                    } else {
                        encoded.append(codeUnit);
                    }
                }
            }
        }
        return encoded.append('"').toString();
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
