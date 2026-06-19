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

    private static final long serialVersionUID = 1L;
}
