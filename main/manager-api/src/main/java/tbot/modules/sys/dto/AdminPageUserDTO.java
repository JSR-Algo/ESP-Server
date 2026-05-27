package tbot.modules.sys.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.Min;
import lombok.Data;

/**
 * Admin paginated user parameter DTO
 * 
 * @author zjy
 * @since 2025-3-21
 */
@Data
@Schema(description = "Admin paginated user parameter DTO")
public class AdminPageUserDTO {

    @Schema(description = "Phone number")
    private String mobile;

    @Schema(description = "Pages")
    @Min(value = 0, message = "{sort.number}")
    private String page;

    @Schema(description = "Display columns")
    @Min(value = 0, message = "{sort.number}")
    private String limit;
}
