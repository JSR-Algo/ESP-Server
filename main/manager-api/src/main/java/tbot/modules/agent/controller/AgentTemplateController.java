package tbot.modules.agent.controller;

import java.util.List;
import java.util.Map;

import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.Parameters;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.AllArgsConstructor;
import tbot.common.constant.Constant;
import tbot.common.page.PageData;
import tbot.common.utils.ConvertUtils;
import tbot.common.utils.Result;
import tbot.common.utils.ResultUtils;
import tbot.modules.agent.entity.AgentTemplateEntity;
import tbot.modules.agent.service.AgentTemplateService;
import tbot.modules.agent.vo.AgentTemplateVO;

@Tag(name = "Agent template management")
@AllArgsConstructor
@RestController
@RequestMapping("/agent/template")
public class AgentTemplateController {
    
    private final AgentTemplateService agentTemplateService;
    
    @GetMapping("/page")
    @Operation(summary = "Get template paged list")
    @RequiresPermissions("sys:role:superAdmin")
    @Parameters({
            @Parameter(name = Constant.PAGE, description = "Current page number, starts from 1", required = true),
            @Parameter(name = Constant.LIMIT, description = "Records per page", required = true),
            @Parameter(name = "agentName", description = "Template name, fuzzy query")
    })
    public Result<PageData<AgentTemplateVO>> getAgentTemplatesPage(
            @Parameter(hidden = true) @RequestParam Map<String, Object> params) {
        
        // CreatePaginationObject
        int page = Integer.parseInt(params.getOrDefault(Constant.PAGE, "1").toString());
        int limit = Integer.parseInt(params.getOrDefault(Constant.LIMIT, "10").toString());
        Page<AgentTemplateEntity> pageInfo = new Page<>(page, limit);
        
        // Create query condition
        QueryWrapper<AgentTemplateEntity> wrapper = new QueryWrapper<>();
        String agentName = (String) params.get("agentName");
        if (agentName != null && !agentName.isEmpty()) {
            wrapper.like("agent_name", agentName);
        }
        wrapper.orderByAsc("sort");
        
        // ExecutePaginationQuery
        IPage<AgentTemplateEntity> pageResult = agentTemplateService.page(pageInfo, wrapper);
        
        // UseConvertUtilsConvert toVOList
        List<AgentTemplateVO> voList = ConvertUtils.sourceToTarget(pageResult.getRecords(), AgentTemplateVO.class);

        // Fix: create with constructorPageDataobject, not no-arg construction+setter
        PageData<AgentTemplateVO> pageData = new PageData<>(voList, pageResult.getTotal());

        return new Result<PageData<AgentTemplateVO>>().ok(pageData);
    }
    
    @GetMapping("/{id}")
    @Operation(summary = "Get template details")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<AgentTemplateVO> getAgentTemplateById(@PathVariable("id") String id) {
        AgentTemplateEntity template = agentTemplateService.getById(id);
        if (template == null) {
            return ResultUtils.error("Template does not exist");
        }
        
        // UseConvertUtilsConvert toVO
        AgentTemplateVO vo = ConvertUtils.sourceToTarget(template, AgentTemplateVO.class);
        
        return ResultUtils.success(vo);
    }
    
    @PostMapping
    @Operation(summary = "Create template")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<AgentTemplateEntity> createAgentTemplate(@Valid @RequestBody AgentTemplateEntity template) {
        // SetSortValue is next available sequence number
        template.setSort(agentTemplateService.getNextAvailableSort());
        
        boolean saved = agentTemplateService.save(template);
        if (saved) {
            return ResultUtils.success(template);
        } else {
            return ResultUtils.error("Create template failed");
        }
    }
    
    @PutMapping
    @Operation(summary = "Update template")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<AgentTemplateEntity> updateAgentTemplate(@Valid @RequestBody AgentTemplateEntity template) {
        boolean updated = agentTemplateService.updateById(template);
        if (updated) {
            return ResultUtils.success(template);
        } else {
            return ResultUtils.error("Update template failed");
        }
    }
    
    @DeleteMapping("/{id}")
    @Operation(summary = "Delete template")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<String> deleteAgentTemplate(@PathVariable("id") String id) {
        // First QueryDeleteTemplate ofInfoGet itsSortvalue
        AgentTemplateEntity template = agentTemplateService.getById(id);
        if (template == null) {
            return ResultUtils.error("Template does not exist");
        }
        
        Integer deletedSort = template.getSort();
        
        // ExecuteDeleteOperation
        boolean deleted = agentTemplateService.removeById(id);
        if (deleted) {
            // DeleteAfter success, reSortRemaining Templates
            agentTemplateService.reorderTemplatesAfterDelete(deletedSort);
            return ResultUtils.success("Template deleted");
        } else {
            return ResultUtils.error("Delete template failed");
        }
    }
    
    
    // Add new batchDeleteMethod, use differentURL
    @PostMapping("/batch-remove")
    @Operation(summary = "Batch delete templates")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<String> batchRemoveAgentTemplates(@RequestBody List<String> ids) {
        boolean deleted = agentTemplateService.removeByIds(ids);
        if (deleted) {
            return ResultUtils.success("Batch delete succeeded");
        } else {
            return ResultUtils.error("Batch delete templates failed");
        }
    }
}