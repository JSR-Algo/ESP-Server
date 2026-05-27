package tbot.modules.sys.dto;

import java.io.Serializable;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

/**
 * Change password
 */
@Data
@Schema(description = "Change password")
public class PasswordDTO implements Serializable {

    @Schema(description = "Old password")
    @NotBlank(message = "{sysuser.password.require}")
    private String password;

    @Schema(description = "New password")
    @NotBlank(message = "{sysuser.password.require}")
    private String newPassword;

}