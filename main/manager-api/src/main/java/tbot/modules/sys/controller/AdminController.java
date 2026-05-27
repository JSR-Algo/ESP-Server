package tbot.modules.sys.controller;

import java.util.Map;

import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.Parameters;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import tbot.common.constant.Constant;
import tbot.common.page.PageData;
import tbot.common.utils.Result;
import tbot.common.validator.ValidatorUtils;
import tbot.modules.device.dto.DevicePageUserDTO;
import tbot.modules.device.service.DeviceService;
import tbot.modules.device.vo.UserShowDeviceListVO;
import tbot.modules.sys.dto.AdminPageUserDTO;
import tbot.modules.sys.service.SysUserService;
import tbot.modules.sys.vo.AdminPageUserVO;

/**
 * Admin control layer
 *
 * @author zjy
 * @since 2025-3-25
 */
@AllArgsConstructor
@RestController
@RequestMapping("/admin")
@Tag(name = "Admin management")
public class AdminController {
    private final SysUserService sysUserService;

    private final DeviceService deviceService;

    @GetMapping("/users")
    @Operation(summary = "Paged user search")
    @RequiresPermissions("sys:role:superAdmin")
    @Parameters({
            @Parameter(name = "mobile", description = "User phone number", required = false),
            @Parameter(name = Constant.PAGE, description = "Current page number, starts from 1", required = true),
            @Parameter(name = Constant.LIMIT, description = "Records per page", required = true),
    })
    public Result<PageData<AdminPageUserVO>> pageUser(
            @Parameter(hidden = true) @RequestParam Map<String, Object> params) {
        AdminPageUserDTO dto = new AdminPageUserDTO();
        dto.setMobile((String) params.get("mobile"));
        dto.setLimit((String) params.get(Constant.LIMIT));
        dto.setPage((String) params.get(Constant.PAGE));
        ValidatorUtils.validateEntity(dto);
        PageData<AdminPageUserVO> page = sysUserService.page(dto);
        return new Result<PageData<AdminPageUserVO>>().ok(page);
    }

    @PutMapping("/users/{id}")
    @Operation(summary = "Reset password")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<String> update(
            @PathVariable Long id) {
        String password = sysUserService.resetPassword(id);
        return new Result<String>().ok(password);
    }

    @DeleteMapping("/users/{id}")
    @Operation(summary = "User deletion")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<Void> delete(@PathVariable Long id) {
        sysUserService.deleteById(id);
        return new Result<>();
    }

    @PutMapping("/users/changeStatus/{status}")
    @Operation(summary = "Batch modify user status")
    @RequiresPermissions("sys:role:superAdmin")
    @Parameter(name = "status", description = "User status", required = true)
    public Result<Void> changeStatus(@PathVariable Integer status, @RequestBody String[] userIds) {
        sysUserService.changeStatus(status, userIds);
        return new Result<Void>();
    }

    @GetMapping("/device/all")
    @Operation(summary = "Paged device search")
    @RequiresPermissions("sys:role:superAdmin")
    @Parameters({
            @Parameter(name = "keywords", description = "Device keyword", required = false),
            @Parameter(name = Constant.PAGE, description = "Current page number, starts from 1", required = true),
            @Parameter(name = Constant.LIMIT, description = "Records per page", required = true),
    })
    public Result<PageData<UserShowDeviceListVO>> pageDevice(
            @Parameter(hidden = true) @RequestParam Map<String, Object> params) {
        DevicePageUserDTO dto = new DevicePageUserDTO();
        dto.setKeywords((String) params.get("keywords"));
        dto.setLimit((String) params.get(Constant.LIMIT));
        dto.setPage((String) params.get(Constant.PAGE));
        ValidatorUtils.validateEntity(dto);
        PageData<UserShowDeviceListVO> page = deviceService.page(dto);
        return new Result<PageData<UserShowDeviceListVO>>().ok(page);
    }
}
