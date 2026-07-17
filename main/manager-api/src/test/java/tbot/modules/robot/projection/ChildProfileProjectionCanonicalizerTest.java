package tbot.modules.robot.projection;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import tbot.modules.robot.projection.ChildProfileProjectionCanonicalizer.ChildProfileProjection;

class ChildProfileProjectionCanonicalizerTest {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    @Test
    void matchesSharedCanonicalProjectionVectors() throws Exception {
        JsonNode fixture = readFixture();

        for (JsonNode vector : fixture.path("vectors")) {
            JsonNode input = vector.path("input");
            ChildProfileProjection profile = input.path("profile").isNull()
                    ? null
                    : profileFrom(input.path("profile"));

            var result = ChildProfileProjectionCanonicalizer.canonicalize(
                    input.path("mode").asText(),
                    input.path("revision").asLong(),
                    profile);

            assertEquals(vector.path("canonicalJson").asText(), result.canonicalJson(), vector.path("name").asText());
            assertEquals(vector.path("sha256").asText(), result.sha256(), vector.path("name").asText());
        }
    }

    @Test
    void rejectsUuidWithUppercaseHex() {
        ChildProfileProjection profile = new ChildProfileProjection(
                "123E4567-E89B-12D3-A456-426614174000",
                "An",
                null,
                List.of(),
                null,
                null,
                null);

        assertThrows(IllegalArgumentException.class,
                () -> ChildProfileProjectionCanonicalizer.canonicalize("replace", 10, profile));
    }

    @ParameterizedTest(name = "rejects {1} surrogate in {0}")
    @MethodSource("unpairedSurrogateCases")
    void rejectsUnpairedSurrogates(String field, String kind, String surrogate) {
        ChildProfileProjection profile = new ChildProfileProjection(
                "123e4567-e89b-12d3-a456-426614174000",
                "displayName".equals(field) ? surrogate : "An",
                null,
                "interests".equals(field) ? List.of(surrogate) : List.of(),
                "learningStyle".equals(field) ? surrogate : null,
                "vocabularyLevel".equals(field) ? surrogate : null,
                "parentCareer".equals(field) ? surrogate : null);

        assertThrows(IllegalArgumentException.class,
                () -> ChildProfileProjectionCanonicalizer.canonicalize("replace", 12, profile));
    }

    private static List<Arguments> unpairedSurrogateCases() {
        return List.of(
                Arguments.of("displayName", "high", "\ud800"),
                Arguments.of("displayName", "low", "\udc00"),
                Arguments.of("interests", "high", "\ud800"),
                Arguments.of("interests", "low", "\udc00"),
                Arguments.of("learningStyle", "high", "\ud800"),
                Arguments.of("learningStyle", "low", "\udc00"),
                Arguments.of("vocabularyLevel", "high", "\ud800"),
                Arguments.of("vocabularyLevel", "low", "\udc00"),
                Arguments.of("parentCareer", "high", "\ud800"),
                Arguments.of("parentCareer", "low", "\udc00"));
    }

    private static JsonNode readFixture() throws Exception {
        try (InputStream stream = ChildProfileProjectionCanonicalizerTest.class.getClassLoader()
                .getResourceAsStream("child-profile-projection-vectors.json")) {
            if (stream == null) {
                throw new IllegalStateException("missing child profile projection fixture");
            }
            return OBJECT_MAPPER.readTree(stream);
        }
    }

    private static ChildProfileProjection profileFrom(JsonNode node) {
        List<String> interests = new ArrayList<>();
        node.path("interests").forEach(value -> interests.add(value.asText()));
        return new ChildProfileProjection(
                node.path("childProfileId").asText(),
                node.path("displayName").asText(),
                node.path("birthYear").isNull() ? null : node.path("birthYear").asInt(),
                interests,
                nullableText(node, "learningStyle"),
                nullableText(node, "vocabularyLevel"),
                nullableText(node, "parentCareer"));
    }

    private static String nullableText(JsonNode node, String field) {
        return node.path(field).isNull() ? null : node.path(field).asText();
    }
}
