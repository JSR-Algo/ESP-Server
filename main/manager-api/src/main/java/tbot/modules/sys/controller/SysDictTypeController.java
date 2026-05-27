package tbot.modules.sys.controller;

import java.util.Map;

import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
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
import tbot.modules.sys.dto.SysDictTypeDTO;
import tbot.modules.sys.service.SysDictTypeService;
import tbot.modules.sys.vo.SysDictTypeVO;

/**
 * Dictionary type management
 *
 * @author czc
 * @since 2025-04-30
 */
@AllArgsConstructor
@RestController
@RequestMapping("/admin/dict/type")
@Tag(name = "Dictionary type management")
public class SysDictTypeController {
    private final SysDictTypeService sysDictTypeService;

    @GetMapping("/page")
    @Operation(summary = "Page query dictionary types")
    @RequiresPermissions("sys:role:superAdmin")
    @Parameters({ @Parameter(name = "dictType", description = "Dictionary type code"),
            @Parameter(name = "dictName", description = "Dictionary type name"),
            @Parameter(name = Constant.PAGE, description = "Current page number, starts from 1", required = true),
            @Parameter(name = Constant.LIMIT, description = "Records per page", required = true) })
    public Result<PageData<SysDictTypeVO>> page(@Parameter(hidden = true) @RequestParam Map<String, Object> params) {
        ValidatorUtils.validateEntity(params);
        PageData<SysDictTypeVO> page = sysDictTypeService.page(params);
        return new Result<PageData<SysDictTypeVO>>().ok(page);
    }

    @GetMapping("/{id}")
    @Operation(summary = "Get dictionary type details")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<SysDictTypeVO> get(@PathVariable("id") Long id) {
        SysDictTypeVO vo = sysDictTypeService.get(id);
        return new Result<SysDictTypeVO>().ok(vo);
    }

    @PostMapping("/save")
    @Operation(summary = "Save dictionary type")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<Void> save(@RequestBody SysDictTypeDTO dto) {
        // Parameter Validation
        ValidatorUtils.validateEntity(dto);

        sysDictTypeService.save(dto);
        return new Result<>();
    }

    @PutMapping("/update")
    @Operation(summary = "Modify dictionary type")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<Void> update(@RequestBody SysDictTypeDTO dto) {
        // Parameter Validation
        ValidatorUtils.validateEntity(dto);

        sysDictTypeService.update(dto);
        return new Result<>();
    }

    @PostMapping("/delete")
    @Operation(summary = "Delete dictionary type")
    @RequiresPermissions("sys:role:superAdmin")
    @Parameter(name = "ids", description = "ID array", required = true)
    public Result<Void> delete(@RequestBody Long[] ids) {
        sysDictTypeService.delete(ids);
        return new Result<>();
    }
}
