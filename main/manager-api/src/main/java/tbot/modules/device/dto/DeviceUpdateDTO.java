package tbot.modules.device.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * Device UpdateDTO
 */
@Data
public class DeviceUpdateDTO implements Serializable {
    /**
    * Auto update status
    */
    @Max(1)
    @Min(0)
    private Integer autoUpdate;

    /**
    * Device Alias
    */
    @Size(max = 64)
    private String alias;

    /**
    * Child display name for personalized conversation
    */
    @Size(max = 64)
    private String childName;

    /**
    * Child age for age-appropriate conversation
    */
    @Max(18)
    @Min(1)
    private Integer childAge;

    /**
    * Comma-separated child interests for lesson personalization
    */
    @Size(max = 255)
    private String childInterests;

    /**
    * Preferred learning style for lesson personalization
    */
    @Size(max = 32)
    private String learningStyle;

    /**
    * Vocabulary level for lesson personalization
    */
    @Size(max = 32)
    private String vocabularyLevel;

    /**
    * Parent career topic for lesson personalization
    */
    @Size(max = 64)
    private String parentCareer;

    private String childProfileId;

    private Integer childBirthYear;

    private Long childProfileRevision;

    @Size(max = 64)
    private String childProfilePayloadHash;

    private String childInterestsJson;

    private static final long serialVersionUID = 1L;
}
