package tbot.common.page;

import java.io.Serializable;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

/**
 * Token info
 *
 * @author Jack
 */
@Data
@Schema(description = "Token info")
public class TokenDTO implements Serializable {

    @Schema(description = "Password")
    private String token;

    @Schema(description = "Expiration time")
    private int expire;

    @Schema(description = "Client fingerprint")
    private String clientHash;
}
