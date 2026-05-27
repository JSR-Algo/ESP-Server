package tbot.common.utils;

import lombok.Data;
/**
 * JSON-RPC2.0 Format spec object
 */
@Data
public class JsonRpcTwo {
    private String jsonrpc = "2.0";
    private String method;
    private Object params;
    private Integer id;

    public JsonRpcTwo(String method, Object params, Integer id) {
        this.method = method;
        this.params = params;
        this.id = id;
    }


}