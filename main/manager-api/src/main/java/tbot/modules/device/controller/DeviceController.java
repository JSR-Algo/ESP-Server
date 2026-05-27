package tbot.modules.device.controller;

import java.util.List;
import java.util.Map;

import org.apache.commons.lang3.StringUtils;
import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.beans.BeanUtils;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import tbot.common.exception.ErrorCode;
import tbot.common.redis.RedisKeys;
import tbot.common.redis.RedisUtils;
import tbot.common.user.UserDetail;
import tbot.common.utils.Result;
import tbot.modules.device.dto.DeviceManualAddDTO;
import tbot.modules.device.dto.DeviceRegisterDTO;
import tbot.modules.device.dto.DeviceToolsCallReqDTO;
import tbot.modules.device.dto.DeviceUnBindDTO;
import tbot.modules.device.dto.DeviceUpdateDTO;
import tbot.modules.device.entity.DeviceEntity;
import tbot.modules.device.service.DeviceService;
import tbot.modules.security.user.SecurityUser;
import tbot.modules.sys.service.SysParamsService;

@Tag(name = "Device management")
@RestController
@RequestMapping("/device")
public class DeviceController {
    private final DeviceService deviceService;
    private final RedisUtils redisUtils;
    private final SysParamsService sysParamsService;

    public DeviceController(DeviceService deviceService, RedisUtils redisUtils, SysParamsService sysParamsService) {
        this.deviceService = deviceService;
        this.redisUtils = redisUtils;
        this.sysParamsService = sysParamsService;
    }

    @PostMapping("/bind/{agentId}/{deviceCode}")
    @Operation(summary = "Bind device")
    @RequiresPermissions("sys:role:normal")
    public Result<Void> bindDevice(@PathVariable String agentId, @PathVariable String deviceCode) {
        deviceService.deviceActivation(agentId, deviceCode);
        return new Result<>();
    }

    @PostMapping("/register")
    @Operation(summary = "Register device")
    public Result<String> registerDevice(@RequestBody DeviceRegisterDTO deviceRegisterDTO) {
        String macAddress = deviceRegisterDTO.getMacAddress();
        if (StringUtils.isBlank(macAddress)) {
            return new Result<String>().error(ErrorCode.MCA_NOT_NULL);
        }
        // Generate Six DigitsVerification code
        String code;
        String key;
        String existsMac = null;
        do {
            code = String.valueOf(Math.random()).substring(2, 8);
            key = RedisKeys.getDeviceCaptchaKey(code);
            existsMac = (String) redisUtils.get(key);
        } while (StringUtils.isNotBlank(existsMac));

        redisUtils.set(key, macAddress);
        return new Result<String>().ok(code);
    }

    @GetMapping("/bind/{agentId}")
    @Operation(summary = "Get bound devices")
    @RequiresPermissions("sys:role:normal")
    public Result<List<DeviceEntity>> getUserDevices(@PathVariable String agentId) {
        UserDetail user = SecurityUser.getUser();
        List<DeviceEntity> devices = deviceService.getUserDevices(user.getId(), agentId);
        return new Result<List<DeviceEntity>>().ok(devices);
    }

    @PostMapping("/bind/{agentId}")
    @Operation(summary = "Device online API")
    @RequiresPermissions("sys:role:normal")
    public Result<String> forwardToMqttGateway(@PathVariable String agentId, @RequestBody String requestBody) {
        try {
            return new Result<String>().ok(deviceService.getDeviceOnlineData(agentId));
        } catch (Exception e) {
            return new Result<String>().error("Forward request failed: " + e.getMessage());
        }
    }

    @PostMapping("/unbind")
    @Operation(summary = "Unbind device")
    @RequiresPermissions("sys:role:normal")
    public Result<Void> unbindDevice(@RequestBody DeviceUnBindDTO unDeviveBind) {
        UserDetail user = SecurityUser.getUser();
        deviceService.unbindDevice(user.getId(), unDeviveBind.getDeviceId());
        return new Result<Void>();
    }

    @PutMapping("/update/{id}")
    @Operation(summary = "Update device info")
    @RequiresPermissions("sys:role:normal")
    public Result<Void> updateDeviceInfo(@PathVariable String id, @Valid @RequestBody DeviceUpdateDTO deviceUpdateDTO) {
        DeviceEntity entity = deviceService.selectById(id);
        if (entity == null) {
            return new Result<Void>().error("Device does not exist");
        }
        UserDetail user = SecurityUser.getUser();
        if (!entity.getUserId().equals(user.getId())) {
            return new Result<Void>().error("Device does not exist");
        }
        BeanUtils.copyProperties(deviceUpdateDTO, entity);
        deviceService.updateById(entity);
        return new Result<Void>();
    }

    @PostMapping("/manual-add")
    @Operation(summary = "Manually add device")
    @RequiresPermissions("sys:role:normal")
    public Result<Void> manualAddDevice(@RequestBody @Valid DeviceManualAddDTO dto) {
        UserDetail user = SecurityUser.getUser();
        deviceService.manualAddDevice(user.getId(), dto);
        return new Result<>();
    }

    @PostMapping("/tools/list/{deviceId}")
    @Operation(summary = "Get device tool list")
    @RequiresPermissions("sys:role:normal")
    public Result<Object> getDeviceTools(@PathVariable String deviceId) {
        Object toolsData = deviceService.getDeviceTools(deviceId);
        if (toolsData == null) {
            return new Result<Object>().error(ErrorCode.DEVICE_NOT_EXIST);
        }

        return new Result<Object>().ok(toolsData);
    }

    @PostMapping("/tools/call/{deviceId}")
    @Operation(summary = "Call device tool")
    @RequiresPermissions("sys:role:normal")
    public Result<Object> callDeviceTool(@PathVariable String deviceId,
            @Valid @RequestBody DeviceToolsCallReqDTO request) {
        String toolName = request.getName();
        Map<String, Object> arguments = request.getArguments();

        Object result = deviceService.callDeviceTool(deviceId, toolName, arguments);
        if (result == null) {
            return new Result<Object>().error(ErrorCode.DEVICE_NOT_EXIST);
        }

        Result<Object> response = new Result<Object>();
        response.setMsg("Tools called successfully");
        return response.ok(result);
    }
}