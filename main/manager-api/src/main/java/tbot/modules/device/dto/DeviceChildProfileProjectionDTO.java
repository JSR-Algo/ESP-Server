package tbot.modules.device.dto;

import java.util.List;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import lombok.Getter;
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
    private final Long revision;

    @NotBlank
    @Pattern(regexp = "[0-9a-f]{64}")
    private final String payloadHash;

    @Valid
    private final Profile profile;

    @JsonCreator
    public DeviceChildProfileProjectionDTO(
            @JsonProperty(value = "mode", required = true) String mode,
            @JsonProperty(value = "revision", required = true) Long revision,
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
            @JsonProperty(value = "displayName", required = true) @NotNull String displayName,
            @JsonProperty(value = "birthYear", required = true) Integer birthYear,
            @JsonProperty(value = "interests", required = true) @NotNull List<@NotNull String> interests,
            @JsonProperty(value = "learningStyle", required = true) String learningStyle,
            @JsonProperty(value = "vocabularyLevel", required = true) String vocabularyLevel,
            @JsonProperty(value = "parentCareer", required = true) String parentCareer) {
        public Profile {
            interests = interests == null ? null : List.copyOf(interests);
        }

        public ChildProfileProjection toCanonicalProfile() {
            return new ChildProfileProjection(childProfileId, displayName, birthYear, interests,
                    learningStyle, vocabularyLevel, parentCareer);
        }
    }
}
