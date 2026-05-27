package tbot.modules.sys.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

import java.io.Serializable;

/**
 * Retrieve passwordDTO
 */
@Data
@Schema(description = "Retrieve password")
public class RetrievePasswordDTO implements Serializable {

    @Schema(description = "Phone number")
    @NotBlank(message = "{sysuser.password.require}")
    private String phone;

    @Schema(description = "Verification code")
    @NotBlank(message = "{sysuser.password.require}")
    private String code;

    @Schema(description = "New password")
    @NotBlank(message = "{sysuser.password.require}")
    private String password;

    @Schema(description = "Graphic captcha ID")
    @NotBlank(message = "{sysuser.uuid.require}")
    private String captchaId;



}