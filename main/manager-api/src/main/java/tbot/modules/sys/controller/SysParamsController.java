package tbot.modules.sys.controller;

import java.util.Map;

import org.apache.commons.lang3.StringUtils;
import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestTemplate;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.Parameters;
import io.swagger.v3.oas.annotations.enums.ParameterIn;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import tbot.common.annotation.LogOperation;
import tbot.common.constant.Constant;
import tbot.common.exception.RenException;
import tbot.common.exception.ErrorCode;
import tbot.common.page.PageData;
import tbot.common.utils.Result;
import tbot.common.validator.AssertUtils;
import tbot.common.validator.ValidatorUtils;
import tbot.common.validator.group.AddGroup;
import tbot.common.validator.group.DefaultGroup;
import tbot.common.validator.group.UpdateGroup;
import tbot.modules.config.service.ConfigService;
import tbot.modules.sys.dto.SysParamsDTO;
import tbot.modules.sys.service.SysParamsService;
import tbot.modules.sys.utils.WebSocketValidator;

/**
 * Parameter management
 *
 * @author Mark sunlightcs@gmail.com
 * @since 1.0.0
 */
@RestController
@RequestMapping("admin/params")
@Tag(name = "Parameter management")
@AllArgsConstructor
public class SysParamsController {
    private final SysParamsService sysParamsService;
    private final ConfigService configService;
    private final RestTemplate restTemplate;

    @GetMapping("page")
    @Operation(summary = "Pagination")
    @Parameters({
            @Parameter(name = Constant.PAGE, description = "Current page number, starts from 1", in = ParameterIn.QUERY, required = true, ref = "int"),
            @Parameter(name = Constant.LIMIT, description = "Records per page", in = ParameterIn.QUERY, required = true, ref = "int"),
            @Parameter(name = Constant.ORDER_FIELD, description = "Sort field", in = ParameterIn.QUERY, ref = "String"),
            @Parameter(name = Constant.ORDER, description = "Sort order, optional values (asc, desc)", in = ParameterIn.QUERY, ref = "String"),
            @Parameter(name = "paramCode", description = "Parameter code or parameter remark", in = ParameterIn.QUERY, ref = "String")
    })
    @RequiresPermissions("sys:role:superAdmin")
    public Result<PageData<SysParamsDTO>> page(@Parameter(hidden = true) @RequestParam Map<String, Object> params) {
        PageData<SysParamsDTO> page = sysParamsService.page(params);

        return new Result<PageData<SysParamsDTO>>().ok(page);
    }

    @GetMapping("{id}")
    @Operation(summary = "Info")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<SysParamsDTO> get(@PathVariable("id") Long id) {
        SysParamsDTO data = sysParamsService.get(id);

        return new Result<SysParamsDTO>().ok(data);
    }

    @PostMapping
    @Operation(summary = "Save")
    @LogOperation("Save")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<Void> save(@RequestBody SysParamsDTO dto) {
        // Validate Data
        ValidatorUtils.validateEntity(dto, AddGroup.class, DefaultGroup.class);

        sysParamsService.save(dto);
        configService.getConfig(false);
        return new Result<Void>();
    }

    @PutMapping
    @Operation(summary = "Modify")
    @LogOperation("Modify")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<Void> update(@RequestBody SysParamsDTO dto) {
        // Validate Data
        ValidatorUtils.validateEntity(dto, UpdateGroup.class, DefaultGroup.class);

        // VerifyWebSocketAddress List
        validateWebSocketUrls(dto.getParamCode(), dto.getParamValue());

        // VerifyOTAAddress
        validateOtaUrl(dto.getParamCode(), dto.getParamValue());

        // VerifyMCPAddress
        validateMcpUrl(dto.getParamCode(), dto.getParamValue());

        // Verify voiceprint URL
        validateVoicePrint(dto.getParamCode(), dto.getParamValue());

        // ValidatemqttKeyLength
        validateMqttSecretLength(dto.getParamCode(), dto.getParamValue());

        sysParamsService.update(dto);
        configService.getConfig(false);
        return new Result<Void>();
    }

    /**
     * VerifyWebSocketAddress List
     *
     * @param urls WebSocketAddress list, separated by semicolons
     * @return Validation Result
     */
    private void validateWebSocketUrls(String paramCode, String urls) {
        if (!paramCode.equals(Constant.SERVER_WEBSOCKET)) {
            return;
        }
        String[] wsUrls = urls.split("\\;");
        if (wsUrls.length == 0) {
            throw new RenException(ErrorCode.WEBSOCKET_URLS_EMPTY);
        }
        for (String url : wsUrls) {
            if (StringUtils.isNotBlank(url)) {
                // Check whether containslocalhostor127.0.0.1
                if (url.contains("localhost") || url.contains("127.0.0.1")) {
                    throw new RenException(ErrorCode.WEBSOCKET_URL_LOCALHOST);
                }

                // VerifyWebSocketAddress Format
                if (!WebSocketValidator.validateUrlFormat(url)) {
                    throw new RenException(ErrorCode.WEBSOCKET_URL_FORMAT_ERROR);
                }

                // TestWebSocketConnect
                if (!WebSocketValidator.testConnection(url)) {
                    throw new RenException(ErrorCode.WEBSOCKET_CONNECTION_FAILED);
                }
            }
        }
    }

    @PostMapping("/delete")
    @Operation(summary = "Delete")
    @LogOperation("Delete")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<Void> delete(@RequestBody String[] ids) {
        // Validate Data
        AssertUtils.isArrayEmpty(ids, "id");

        sysParamsService.delete(ids);
        configService.getConfig(false);
        return new Result<Void>();
    }

    /**
     * VerifyOTAAddress
     */
    private void validateOtaUrl(String paramCode, String url) {
        if (!paramCode.equals(Constant.SERVER_OTA)) {
            return;
        }
        if (StringUtils.isBlank(url) || url.equals("null")) {
            return;
        }

        // Check whether containslocalhostor127.0.0.1
        if (url.contains("localhost") || url.contains("127.0.0.1")) {
            throw new RenException(ErrorCode.OTA_URL_LOCALHOST);
        }

        // VerifyURLFormat
        if (!url.toLowerCase().startsWith("http")) {
            throw new RenException(ErrorCode.OTA_URL_PROTOCOL_ERROR);
        }
        if (!url.endsWith("/ota/")) {
            throw new RenException(ErrorCode.OTA_URL_FORMAT_ERROR);
        }

        try {
            // SendGETRequest
            ResponseEntity<String> response = restTemplate.getForEntity(url, String.class);
            if (response.getStatusCode() != HttpStatus.OK) {
                throw new RenException(ErrorCode.OTA_INTERFACE_ACCESS_FAILED);
            }
            // CheckResponseContentContainsOTARelatedInfo
            String body = response.getBody();
            if (body == null || !body.contains("OTA")) {
                throw new RenException(ErrorCode.OTA_INTERFACE_FORMAT_ERROR);
            }
        } catch (Exception e) {
            throw new RenException(ErrorCode.OTA_INTERFACE_VALIDATION_FAILED);
        }
    }

    private void validateMcpUrl(String paramCode, String url) {
        if (!paramCode.equals(Constant.SERVER_MCP_ENDPOINT)) {
            return;
        }
        if (StringUtils.isBlank(url) || url.equals("null")) {
            return;
        }
        if (url.contains("localhost") || url.contains("127.0.0.1")) {
            throw new RenException(ErrorCode.MCP_URL_LOCALHOST);
        }
        if (!url.toLowerCase().contains("key")) {
            throw new RenException(ErrorCode.MCP_URL_INVALID);
        }

        try {
            // SendGETRequest
            ResponseEntity<String> response = restTemplate.getForEntity(url, String.class);
            if (response.getStatusCode() != HttpStatus.OK) {
                throw new RenException(ErrorCode.MCP_INTERFACE_ACCESS_FAILED);
            }
            // CheckResponseContentContainsmcpRelatedInfo
            String body = response.getBody();
            if (body == null || !body.contains("success")) {
                throw new RenException(ErrorCode.MCP_INTERFACE_FORMAT_ERROR);
            }
        } catch (Exception e) {
            throw new RenException(ErrorCode.MCP_INTERFACE_VALIDATION_FAILED);
        }
    }

    // Verify voiceprint API address is normal
    private void validateVoicePrint(String paramCode, String url) {
        if (!paramCode.equals(Constant.SERVER_VOICE_PRINT)) {
            return;
        }
        if (StringUtils.isBlank(url) || url.equals("null")) {
            return;
        }
        if (url.contains("localhost") || url.contains("127.0.0.1")) {
            throw new RenException(ErrorCode.VOICEPRINT_URL_LOCALHOST);
        }
        if (!url.toLowerCase().contains("key")) {
            throw new RenException(ErrorCode.VOICEPRINT_URL_INVALID);
        }
        // VerifyURLFormat
        if (!url.toLowerCase().startsWith("http")) {
            throw new RenException(ErrorCode.VOICEPRINT_URL_PROTOCOL_ERROR);
        }
        try {
            // SendGETRequest
            ResponseEntity<String> response = restTemplate.getForEntity(url, String.class);
            if (response.getStatusCode() != HttpStatus.OK) {
                throw new RenException(ErrorCode.VOICEPRINT_INTERFACE_ACCESS_FAILED);
            }
            // CheckResponseContent
            String body = response.getBody();
            if (body == null || !body.contains("healthy")) {
                throw new RenException(ErrorCode.VOICEPRINT_INTERFACE_FORMAT_ERROR);
            }
        } catch (Exception e) {
            throw new RenException(ErrorCode.VOICEPRINT_INTERFACE_VALIDATION_FAILED);
        }
    }

    // ValidatemqttKeyLength and complexity
    private void validateMqttSecretLength(String paramCode, String secret) {
        if (!paramCode.equals(Constant.SERVER_MQTT_SECRET)) {
            return;
        }
        if (StringUtils.isBlank(secret) || secret.equals("null")) {
            throw new RenException(ErrorCode.MQTT_SECRET_EMPTY);
        }
        if (secret.length() < 8) {
            throw new RenException(ErrorCode.MQTT_SECRET_LENGTH_INSECURE);
        }
        // Check whether contains uppercase and lowercase letters
        if (!secret.matches(".*[a-z].*") || !secret.matches(".*[A-Z].*")) {
            throw new RenException(ErrorCode.MQTT_SECRET_CHARACTER_INSECURE);
        }
        // Not allow weakPassword
        String[] weakPasswords = { "test", "1234", "admin", "password", "qwerty", "tbot" };
        for (String weakPassword : weakPasswords) {
            if (secret.toLowerCase().contains(weakPassword)) {
                throw new RenException(ErrorCode.MQTT_SECRET_WEAK_PASSWORD);
            }
        }
    }
}
