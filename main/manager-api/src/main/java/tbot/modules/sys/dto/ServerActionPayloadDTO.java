package tbot.modules.sys.dto;

import lombok.Data;
import tbot.modules.sys.enums.ServerActionEnum;

import java.util.Map;

/**
 * Server actionDTO
 */
@Data
public class ServerActionPayloadDTO
{
    /**
    * Type (Control Panel sends to server all areserver)
    */
    private String type;
    /**
    * Action
    */
    private ServerActionEnum action;
    /**
    * Content
    */
    private Map<String, Object> content;

    public static ServerActionPayloadDTO build(ServerActionEnum action, Map<String, Object> content) {
        ServerActionPayloadDTO serverActionPayloadDTO = new ServerActionPayloadDTO();
        serverActionPayloadDTO.setAction(action);
        serverActionPayloadDTO.setContent(content);
        serverActionPayloadDTO.setType("server");
        return serverActionPayloadDTO;
    }
    // Privatization
    private ServerActionPayloadDTO() {}
}
