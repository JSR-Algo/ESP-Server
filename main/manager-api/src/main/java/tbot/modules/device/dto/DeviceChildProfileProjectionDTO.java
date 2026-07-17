package tbot.modules.device.dto;

import java.io.IOException;
import java.math.BigInteger;
import java.util.List;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.JsonToken;
import com.fasterxml.jackson.databind.DeserializationContext;
import com.fasterxml.jackson.databind.JsonDeserializer;
import com.fasterxml.jackson.databind.JsonMappingException;
import com.fasterxml.jackson.databind.annotation.JsonDeserialize;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import lombok.Getter;
import tbot.modules.device.dto.validation.CodePointSize;
import tbot.modules.robot.projection.ChildProfileProjectionCanonicalizer.ChildProfileProjection;

@Getter
@JsonIgnoreProperties(ignoreUnknown = false)
public final class DeviceChildProfileProjectionDTO {
    @NotBlank
    @Pattern(regexp = "replace|clear")
    private final String mode;

    @NotNull
    @Min(0)
    @Max(9007199254740991L)
    @JsonDeserialize(using = StrictRevisionDeserializer.class)
    private final Long revision;

    @NotBlank
    @Pattern(regexp = "[0-9a-f]{64}")
    private final String payloadHash;

    @Valid
    private final Profile profile;

    @JsonCreator
    public DeviceChildProfileProjectionDTO(
            @JsonProperty(value = "mode", required = true) String mode,
            @JsonProperty(value = "revision", required = true)
            @JsonDeserialize(using = StrictRevisionDeserializer.class) Long revision,
            @JsonProperty(value = "payloadHash", required = true) String payloadHash,
            @JsonProperty(value = "profile", required = true) Profile profile) {
        this.mode = mode;
        this.revision = revision;
        this.payloadHash = payloadHash;
        this.profile = profile;
    }

    public DeviceChildProfileProjectionDTO(String mode, long revision, String payloadHash, Profile profile) {
        this(mode, Long.valueOf(revision), payloadHash, profile);
    }

    @JsonIgnoreProperties(ignoreUnknown = false)
    public record Profile(
            @JsonProperty(value = "childProfileId", required = true)
            @NotBlank @Pattern(regexp = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}") String childProfileId,
            @JsonProperty(value = "displayName", required = true)
            @NotNull @CodePointSize(max = 64) String displayName,
            @JsonProperty(value = "birthYear", required = true)
            @JsonDeserialize(using = StrictBirthYearDeserializer.class) Integer birthYear,
            @JsonProperty(value = "interests", required = true)
            @NotNull @Size(max = 256) List<@NotNull @CodePointSize(max = 4096) String> interests,
            @JsonProperty(value = "learningStyle", required = true)
            @CodePointSize(max = 32) String learningStyle,
            @JsonProperty(value = "vocabularyLevel", required = true)
            @CodePointSize(max = 32) String vocabularyLevel,
            @JsonProperty(value = "parentCareer", required = true)
            @CodePointSize(max = 64) String parentCareer) {
        public Profile {
            interests = interests == null ? null : List.copyOf(interests);
        }

        public ChildProfileProjection toCanonicalProfile() {
            return new ChildProfileProjection(childProfileId, displayName, birthYear, interests,
                    learningStyle, vocabularyLevel, parentCareer);
        }
    }

    public static final class StrictRevisionDeserializer extends JsonDeserializer<Long> {
        private static final BigInteger MAX_REVISION = BigInteger.valueOf(9007199254740991L);

        @Override
        public Long deserialize(JsonParser parser, DeserializationContext context) throws IOException {
            BigInteger value = strictInteger(parser, "revision");
            if (value.signum() < 0 || value.compareTo(MAX_REVISION) > 0) {
                throw JsonMappingException.from(parser, "revision is outside the supported range");
            }
            return value.longValueExact();
        }
    }

    public static final class StrictBirthYearDeserializer extends JsonDeserializer<Integer> {
        private static final BigInteger MIN = BigInteger.valueOf(Integer.MIN_VALUE);
        private static final BigInteger MAX = BigInteger.valueOf(Integer.MAX_VALUE);

        @Override
        public Integer deserialize(JsonParser parser, DeserializationContext context) throws IOException {
            if (parser.currentToken() == JsonToken.VALUE_NULL) {
                return null;
            }
            BigInteger value = strictInteger(parser, "birthYear");
            if (value.compareTo(MIN) < 0 || value.compareTo(MAX) > 0) {
                throw JsonMappingException.from(parser, "birthYear is outside the signed 32-bit range");
            }
            return value.intValueExact();
        }
    }

    private static BigInteger strictInteger(JsonParser parser, String field) throws IOException {
        if (parser.currentToken() != JsonToken.VALUE_NUMBER_INT) {
            throw JsonMappingException.from(parser, field + " must be encoded as a JSON integer");
        }
        return parser.getBigIntegerValue();
    }
}
