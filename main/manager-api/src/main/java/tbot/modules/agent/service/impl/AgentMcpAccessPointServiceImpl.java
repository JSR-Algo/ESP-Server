package tbot.modules.agent.service.impl;

import java.net.URI;
import java.net.URISyntaxException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Service;

import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import tbot.common.constant.Constant;
import tbot.common.utils.AESUtils;
import tbot.common.utils.HashEncryptionUtil;
import tbot.common.utils.JsonUtils;
import tbot.modules.agent.Enums.TBotMcpJsonRpcJson;
import tbot.modules.agent.service.AgentMcpAccessPointService;
import tbot.modules.sys.service.SysParamsService;
import tbot.modules.sys.utils.WebSocketClientManager;

@AllArgsConstructor
@Service
@Slf4j
public class AgentMcpAccessPointServiceImpl implements AgentMcpAccessPointService {
    private SysParamsService sysParamsService;

    @Override
    public String getAgentMcpAccessAddress(String id) {
        // GotmcpAddress of
        String url = sysParamsService.getValue(Constant.SERVER_MCP_ENDPOINT, true);
        if (StringUtils.isBlank(url) || "null".equals(url)) {
            return null;
        }
        URI uri = getURI(url);
        // Get agentmcpofurlPrefix
        String agentMcpUrl = getAgentMcpUrl(uri);
        // GetKey
        String key = getSecretKey(uri);
        // Get encryptedtoken
        String encryptToken = encryptToken(id, key);
        // pairtokenPerformURLEncode
        String encodedToken = URLEncoder.encode(encryptToken, StandardCharsets.UTF_8);
        // Return agentMcpPath format
        agentMcpUrl = "%s/mcp/?token=%s".formatted(agentMcpUrl, encodedToken);
        return agentMcpUrl;
    }

    @Override
    public List<String> getAgentMcpToolsList(String id) {
        String wsUrl = getAgentMcpAccessAddress(id);
        if (StringUtils.isBlank(wsUrl)) {
            return List.of();
        }

        // will /mcp Replace with /call
        wsUrl = wsUrl.replace("/mcp/", "/call/");

        try {
            // Create WebSocket connection, increase timeout to15seconds
            try (WebSocketClientManager client = WebSocketClientManager.build(
                    new WebSocketClientManager.Builder()
                            .uri(wsUrl)
                            .bufferSize(1024 * 1024)
                            .connectTimeout(8, TimeUnit.SECONDS)
                            .maxSessionDuration(10, TimeUnit.SECONDS))) {

                // Step1: Send initializationMessageAnd waitPending response
                log.info("Send MCP initialization message, agent ID: {}", id);
                client.sendText(TBotMcpJsonRpcJson.getInitializeJson());

                // Wait initializationResponse (id=1) - Remove fixed delay, change toResponseDrive
                List<String> initResponses = client.listenerWithoutClose(response -> {
                    try {
                        Map<String, Object> jsonMap = JsonUtils.parseObject(response, Map.class);
                        if (jsonMap != null && Integer.valueOf(1).equals(jsonMap.get("id"))) {
                            // Check whether hasresultfield, indicates initialization succeeded
                            return jsonMap.containsKey("result") && !jsonMap.containsKey("error");
                        }
                        return false;
                    } catch (Exception e) {
                        log.warn("Parse init response failed: {}", response, e);
                        return false;
                    }
                });

                // Validate initializationResponse
                boolean initSucceeded = false;
                for (String response : initResponses) {
                    try {
                        Map<String, Object> jsonMap = JsonUtils.parseObject(response, Map.class);
                        if (jsonMap != null && Integer.valueOf(1).equals(jsonMap.get("id"))) {
                            if (jsonMap.containsKey("result")) {
                                log.info("MCP initialization successful, agent ID: {}", id);
                                initSucceeded = true;
                                break;
                            } else if (jsonMap.containsKey("error")) {
                                log.error("MCP initialization failed, agent ID: {}, error: {}", id, jsonMap.get("error"));
                                return List.of();
                            }
                        }
                    } catch (Exception e) {
                        log.warn("Process init response failed: {}", response, e);
                    }
                }

                if (!initSucceeded) {
                    log.error("No valid MCP initialization response received, agent ID: {}", id);
                    return List.of();
                }

                // Step2: Send initialization complete notification - Only after receivedinitializeResponsesend after
                log.info("Send MCP initialization complete notification, agent ID: {}", id);
                client.sendText(TBotMcpJsonRpcJson.getNotificationsInitializedJson());
                // Step3: Send tool list request - Send immediately, no extra delay needed
                log.info("Send MCP tool list request, agent ID: {}", id);
                client.sendText(TBotMcpJsonRpcJson.getToolsListJson());

                // Wait tool listResponse (id=2)
                List<String> toolsResponses = client.listener(response -> {
                    try {
                        Map<String, Object> jsonMap = JsonUtils.parseObject(response, Map.class);
                        return jsonMap != null && Integer.valueOf(2).equals(jsonMap.get("id"));
                    } catch (Exception e) {
                        log.warn("Parse tool list response failed: {}", response, e);
                        return false;
                    }
                });

                // Handle tool listResponse
                for (String response : toolsResponses) {
                    try {
                        Map<String, Object> jsonMap = JsonUtils.parseObject(response, Map.class);
                        if (jsonMap != null && Integer.valueOf(2).equals(jsonMap.get("id"))) {
                            // Check whether hasresultField
                            Object resultObj = jsonMap.get("result");
                            if (resultObj instanceof Map) {
                                Map<String, Object> resultMap = (Map<String, Object>) resultObj;
                                Object toolsObj = resultMap.get("tools");
                                if (toolsObj instanceof List) {
                                    List<Map<String, Object>> toolsList = (List<Map<String, Object>>) toolsObj;
                                    // Extract ToolNameList
                                    List<String> result = toolsList.stream()
                                            .map(tool -> (String) tool.get("name"))
                                            .filter(name -> name != null)
                                            .sorted()
                                            .collect(Collectors.toList());
                                    log.info("Successfully got MCP tool list, agent ID: {}, tool count: {}", id, result.size());
                                    return result;
                                }
                            } else if (jsonMap.containsKey("error")) {
                                log.error("Failed to get tool list, agent ID: {}, error: {}", id, jsonMap.get("error"));
                                return List.of();
                            }
                        }
                    } catch (Exception e) {
                        log.warn("Process tool list response failed: {}", response, e);
                    }
                }

                log.warn("No valid tool list response found, agent ID: {}", id);
                return List.of();

            }
        } catch (Exception e) {
            log.error("Failed to get agent MCP tool list, agent ID: {}, error reason: {}", id, e.getMessage());
            return List.of();
        }
    }

    /**
     * GetURIObject
     * 
     * @param url Path
     * @return URIObject
     */
    private static URI getURI(String url) {
        try {
            return new URI(url);
        } catch (URISyntaxException e) {
            log.error("Path format incorrect path: {},\nerror info:{}", url, e.getMessage());
            throw new RuntimeException("mcp address is wrong, go to parameter management to modify mcp access point address");
        }
    }

    /**
     * GetKey
     *
     * @param uri mcpAddress
     * @return Key
     */
    private static String getSecretKey(URI uri) {
        // Get Parameters
        String query = uri.getQuery();
        // GetaesEncryptKey
        String str = "key=";
        return query.substring(query.indexOf(str) + str.length());
    }

    /**
     * Get agentmcpAccess pointurl
     *
     * @param uri mcpAddress
     * @return AgentmcpAccess pointurl
     */
    private String getAgentMcpUrl(URI uri) {
        // Get Protocol
        String wsScheme = (uri.getScheme().equals("https")) ? "wss" : "ws";
        // Get host, port, path
        String path = uri.getSchemeSpecificPart();
        // Got last one/Beforepath
        path = path.substring(0, path.lastIndexOf("/"));
        return wsScheme + ":" + path;
    }

    /**
     * Get forAgent idEncryptedtoken
     *
     * @param agentId Agent id
     * @param key     EncryptKey
     * @return After encryptiontoken
     */
    private static String encryptToken(String agentId, String key) {
        // Usemd5pairAgent idEncrypt
        String md5 = HashEncryptionUtil.Md5hexDigest(agentId);
        // aesText to encrypt
        String json = "{\"agentId\": \"%s\"}".formatted(md5);
        // Encrypted Intotokenvalue
        return AESUtils.encrypt(key, json);
    }
}