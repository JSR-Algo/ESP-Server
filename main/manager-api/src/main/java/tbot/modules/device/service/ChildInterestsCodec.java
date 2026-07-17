package tbot.modules.device.service;

import java.util.Arrays;
import java.util.List;
import java.util.Objects;

import org.apache.commons.lang3.StringUtils;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

public final class ChildInterestsCodec {
    private static final ObjectMapper JSON = new ObjectMapper();

    private ChildInterestsCodec() {
    }

    public static String encode(List<String> interests) {
        Objects.requireNonNull(interests, "interests");
        if (interests.stream().anyMatch(Objects::isNull)) {
            throw new IllegalArgumentException("interests cannot contain null values");
        }
        try {
            return JSON.writeValueAsString(interests);
        } catch (Exception error) {
            throw new IllegalArgumentException("interests cannot be encoded", error);
        }
    }

    public static List<String> decodeJsonOrLegacy(String encoded) {
        if (encoded == null || encoded.isEmpty()) {
            return List.of();
        }
        if (encoded.stripLeading().startsWith("[")) {
            return decodeJson(encoded);
        }
        return Arrays.stream(encoded.split(","))
                .map(StringUtils::trimToNull)
                .filter(Objects::nonNull)
                .toList();
    }

    private static List<String> decodeJson(String encoded) {
        try {
            JsonNode root = JSON.readTree(encoded);
            if (!root.isArray()) {
                throw new IllegalArgumentException("stored interests JSON must be an array");
            }
            var values = new java.util.ArrayList<String>(root.size());
            for (JsonNode item : root) {
                if (!item.isTextual()) {
                    throw new IllegalArgumentException("stored interests JSON must contain only strings");
                }
                values.add(item.textValue());
            }
            return List.copyOf(values);
        } catch (IllegalArgumentException error) {
            throw error;
        } catch (Exception error) {
            throw new IllegalArgumentException("stored interests JSON is invalid", error);
        }
    }
}
