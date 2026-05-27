package tbot.modules.agent.controller;

import java.util.List;

import org.apache.commons.lang3.StringUtils;
import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.web.bind.annotation.DeleteMapping;
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
import lombok.AllArgsConstructor;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.utils.Result;
import tbot.modules.agent.dto.AgentVoicePrintSaveDTO;
import tbot.modules.agent.dto.AgentVoicePrintUpdateDTO;
import tbot.modules.agent.service.AgentVoicePrintService;
import tbot.modules.agent.vo.AgentVoicePrintVO;
import tbot.modules.security.user.SecurityUser;
import tbot.modules.sys.service.SysParamsService;

@Tag(name = "Agent voiceprint management")
@AllArgsConstructor
@RestController
@RequestMapping("/agent/voice-print")
public class AgentVoicePrintController {
    private final AgentVoicePrintService agentVoicePrintService;
    private final SysParamsService sysParamsService;

    @PostMapping
    @Operation(summary = "Create agent voiceprint")
    @RequiresPermissions("sys:role:normal")
    public Result<Void> save(@RequestBody @Valid AgentVoicePrintSaveDTO dto) {
        boolean b = agentVoicePrintService.insert(dto);
        if (b) {
            return new Result<>();
        }
        return new Result<Void>().error(ErrorCode.AGENT_VOICEPRINT_CREATE_FAILED);
    }

    @PutMapping
    @Operation(summary = "Update corresponding voiceprint of agent")
    @RequiresPermissions("sys:role:normal")
    public Result<Void> update(@RequestBody @Valid AgentVoicePrintUpdateDTO dto) {
        Long userId = SecurityUser.getUserId();
        boolean b = agentVoicePrintService.update(userId, dto);
        if (b) {
            return new Result<>();
        }
        return new Result<Void>().error(ErrorCode.AGENT_VOICEPRINT_UPDATE_FAILED);
    }

    @DeleteMapping("/{id}")
    @Operation(summary = "Delete agent voiceprint")
    @RequiresPermissions("sys:role:normal")
    public Result<Void> delete(@PathVariable String id) {
        Long userId = SecurityUser.getUserId();
        // firstDeleteAssociated device
        boolean delete = agentVoicePrintService.delete(userId, id);
        if (delete) {
            return new Result<>();
        }
        return new Result<Void>().error(ErrorCode.AGENT_VOICEPRINT_DELETE_FAILED);
    }

    @GetMapping("/list/{id}")
    @Operation(summary = "Get user-specified agent voiceprint list")
    @RequiresPermissions("sys:role:normal")
    public Result<List<AgentVoicePrintVO>> list(@PathVariable String id) {
        String voiceprintUrl = sysParamsService.getValue("server.voice_print", true);
        if (StringUtils.isBlank(voiceprintUrl) || "null".equals(voiceprintUrl)) {
            throw new RenException(ErrorCode.VOICEPRINT_API_NOT_CONFIGURED);
        }
        Long userId = SecurityUser.getUserId();
        List<AgentVoicePrintVO> list = agentVoicePrintService.list(userId, id);
        return new Result<List<AgentVoicePrintVO>>().ok(list);
    }

}
