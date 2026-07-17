package tbot.modules.robot.projection;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;

import org.junit.jupiter.api.Test;

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
