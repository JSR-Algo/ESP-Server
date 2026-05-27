package tbot.modules.sys.controller;

import java.util.*;
import java.util.concurrent.TimeUnit;

import org.apache.commons.lang3.StringUtils;
import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.socket.WebSocketHttpHeaders;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.AllArgsConstructor;
import tbot.common.annotation.LogOperation;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.utils.Result;
import tbot.modules.sys.dto.EmitSeverActionDTO;
import tbot.modules.sys.dto.ServerActionPayloadDTO;
import tbot.modules.sys.dto.ServerActionResponseDTO;
import tbot.modules.sys.enums.ServerActionEnum;
import tbot.modules.sys.service.SysParamsService;
import tbot.modules.sys.utils.WebSocketClientManager;
import tbot.modules.device.service.DeviceService;
import tbot.common.redis.RedisUtils;

/**
 * Server managementController
 */
@RestController
@RequestMapping("/admin/server")
@Tag(name = "Server management")
@AllArgsConstructor
public class ServerSideManageController {
    private final SysParamsService sysParamsService;
    private final DeviceService deviceService;
    private final RedisUtils redisUtils;
    private static final ObjectMapper objectMapper;
    static {
        objectMapper = new ObjectMapper();
        // IgnorejsonExists in string, butpojocase where corresponding field does not exist in
        objectMapper.configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);
    }

    @Operation(summary = "Get Ws server list")
    @GetMapping("/server-list")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<List<String>> getWsServerList() {
        String wsText = sysParamsService.getValue(Constant.SERVER_WEBSOCKET, true);
        if (StringUtils.isBlank(wsText)) {
            return new Result<List<String>>().ok(Collections.emptyList());
        }
        return new Result<List<String>>().ok(Arrays.asList(wsText.split(";")));
    }

    @Operation(summary = "Notify python server to update config")
    @PostMapping("/emit-action")
    @LogOperation("Notify python server to update config")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<Boolean> emitServerAction(@RequestBody @Valid EmitSeverActionDTO emitSeverActionDTO) {
        if (emitSeverActionDTO.getAction() == null) {
            throw new RenException(ErrorCode.INVALID_SERVER_ACTION);
        }
        String wsText = sysParamsService.getValue(Constant.SERVER_WEBSOCKET, true);
        if (StringUtils.isBlank(wsText)) {
            throw new RenException(ErrorCode.SERVER_WEBSOCKET_NOT_CONFIGURED);
        }
        String targetWs = emitSeverActionDTO.getTargetWs();
        String[] wsList = wsText.split(";");
        // Find need initiate
        if (StringUtils.isBlank(targetWs) || !Arrays.asList(wsList).contains(targetWs)) {
            throw new RenException(ErrorCode.TARGET_WEBSOCKET_NOT_EXIST);
        }
        return new Result<Boolean>().ok(emitServerActionByWs(targetWs, emitSeverActionDTO.getAction()));
    }

    private Boolean emitServerActionByWs(String targetWsUri, ServerActionEnum actionEnum) {
        if (StringUtils.isBlank(targetWsUri) || actionEnum == null) {
            return false;
        }
        String serverSK = sysParamsService.getValue(Constant.SERVER_SECRET, true);

        String deviceId = UUID.randomUUID().toString();
        String clientId = UUID.randomUUID().toString();

        String redisKey = tbot.common.redis.RedisKeys.getTmpRegisterMacKey(deviceId);
        redisUtils.set(redisKey, "true", 300); // 5Minute validity

        WebSocketHttpHeaders headers = new WebSocketHttpHeaders();
        headers.add("device-id", deviceId);
        headers.add("client-id", clientId);
        try {
            String token = deviceService.generateWebSocketToken(clientId, deviceId);
            headers.add("authorization", "Bearer " + token);
        } catch (Exception e) {
            throw new RenException(ErrorCode.WEB_SOCKET_CONNECT_FAILED);
        }

        try (WebSocketClientManager client = new WebSocketClientManager.Builder()
                .connectTimeout(3, TimeUnit.SECONDS)
                .maxSessionDuration(120, TimeUnit.SECONDS)
                .uri(targetWsUri)
                .headers(headers)
                .build()) {
            // If connection succeeds, send onejsonData packet and wait serverResponse
            client.sendJson(
                    ServerActionPayloadDTO.build(
                            actionEnum,
                            Map.of("secret", serverSK)));
            // Wait for serverResponseAnd keep listeningInfo
            client.listener((jsonText) -> {
                if (StringUtils.isBlank(jsonText)) {
                    return false;
                }
                try {
                    ServerActionResponseDTO response = objectMapper.readValue(jsonText, ServerActionResponseDTO.class);
                    Boolean isSuccess = ServerActionResponseDTO.isSuccess(response);
                    return isSuccess;
                } catch (JsonProcessingException e) {
                    return false;
                }
            });
        } catch (Exception e) {
            // Catch AllError, by globalExceptionHandler returns
            throw new RenException(ErrorCode.WEB_SOCKET_CONNECT_FAILED);
        }
        return true;
    }
}
