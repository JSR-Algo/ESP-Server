package tbot.modules.sys.controller;

import java.util.List;
import java.util.Map;

import org.apache.commons.lang3.StringUtils;
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
import tbot.modules.sys.dto.SysDictDataDTO;
import tbot.modules.sys.service.SysDictDataService;
import tbot.modules.sys.vo.SysDictDataItem;
import tbot.modules.sys.vo.SysDictDataVO;

/**
 * Dictionary data management
 *
 * @author czc
 * @since 2025-04-30
 */
@AllArgsConstructor
@RestController
@RequestMapping("/admin/dict/data")
@Tag(name = "Dictionary data management")
public class SysDictDataController {
    private final SysDictDataService sysDictDataService;

    @GetMapping("/page")
    @Operation(summary = "Page query dictionary data")
    @RequiresPermissions("sys:role:superAdmin")
    @Parameters({ @Parameter(name = "dictTypeId", description = "Dictionary type ID", required = true),
            @Parameter(name = "dictLabel", description = "Data tag"), @Parameter(name = "dictValue", description = "Data value"),
            @Parameter(name = Constant.PAGE, description = "Current page number, starts from 1", required = true),
            @Parameter(name = Constant.LIMIT, description = "Records per page", required = true) })
    public Result<PageData<SysDictDataVO>> page(@Parameter(hidden = true) @RequestParam Map<String, Object> params) {
        ValidatorUtils.validateEntity(params);
        // Force ValidationdictTypeIdExists
        if (!params.containsKey("dictTypeId") || StringUtils.isEmpty(String.valueOf(params.get("dictTypeId")))) {
            return new Result<PageData<SysDictDataVO>>().error("dictTypeId cannot be empty");
        }

        PageData<SysDictDataVO> page = sysDictDataService.page(params);
        return new Result<PageData<SysDictDataVO>>().ok(page);
    }

    @GetMapping("/{id}")
    @Operation(summary = "Get dictionary data details")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<SysDictDataVO> get(@PathVariable("id") Long id) {
        SysDictDataVO vo = sysDictDataService.get(id);
        return new Result<SysDictDataVO>().ok(vo);
    }

    @PostMapping("/save")
    @Operation(summary = "Add dictionary data")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<Void> save(@RequestBody SysDictDataDTO dto) {
        ValidatorUtils.validateEntity(dto);
        sysDictDataService.save(dto);
        return new Result<>();
    }

    @PutMapping("/update")
    @Operation(summary = "Modify dictionary data")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<Void> update(@RequestBody SysDictDataDTO dto) {
        ValidatorUtils.validateEntity(dto);
        sysDictDataService.update(dto);
        return new Result<>();
    }

    @PostMapping("/delete")
    @Operation(summary = "Delete dictionary data")
    @RequiresPermissions("sys:role:superAdmin")
    @Parameter(name = "ids", description = "ID array", required = true)
    public Result<Void> delete(@RequestBody Long[] ids) {
        sysDictDataService.delete(ids);
        return new Result<>();
    }

    @GetMapping("/type/{dictType}")
    @Operation(summary = "Get dictionary data list")
    @RequiresPermissions("sys:role:normal")
    public Result<List<SysDictDataItem>> getDictDataByType(@PathVariable("dictType") String dictType) {
        List<SysDictDataItem> list = sysDictDataService.getDictDataByType(dictType);
        return new Result<List<SysDictDataItem>>().ok(list);
    }

}
