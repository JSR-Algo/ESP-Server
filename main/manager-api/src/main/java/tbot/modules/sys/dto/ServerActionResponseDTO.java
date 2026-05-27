package tbot.modules.sys.dto;

import lombok.Data;
import tbot.modules.sys.enums.ServerActionResponseEnum;

import java.util.Map;

/**
 * Server action response body
 */
@Data
public class ServerActionResponseDTO
{
    private ServerActionResponseEnum status;
    private String message;
    private String type;
    private Map<String, Object> content; // This field can be removed later, and this class used as base class. Write own for businesscontentType
    public static final String DEFAULT_TYPE_FORM_SERVER = "server";

    public static Boolean isSuccess(ServerActionResponseDTO actionResponseDTO) {
        System.out.println(actionResponseDTO);
        if (actionResponseDTO == null) {
            return false;
        }
        if (actionResponseDTO.getStatus() == null || !actionResponseDTO.getStatus().equals(ServerActionResponseEnum.SUCCESS)) {
            return false;
        }
        Object actionType = actionResponseDTO.getContent().get("action");
        if (actionType == null) {
            return false;
        }
        return actionResponseDTO.getType() != null && actionResponseDTO.getType().equals(DEFAULT_TYPE_FORM_SERVER);
    }
}
