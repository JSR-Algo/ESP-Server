package tbot.modules.voiceclone.controller;

import java.util.List;
import java.util.Map;

import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.Parameters;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.page.PageData;
import tbot.common.utils.Result;
import tbot.common.validator.ValidatorUtils;
import tbot.modules.model.service.ModelConfigService;
import tbot.modules.voiceclone.dto.VoiceCloneDTO;
import tbot.modules.voiceclone.dto.VoiceCloneResponseDTO;
import tbot.modules.voiceclone.service.VoiceCloneService;

@Tag(name = "Voice resource management", description = "Voice resource activation APIs")
@Slf4j
@RestController
@RequiredArgsConstructor
@RequestMapping("/voiceResource")
public class VoiceResourceController {

    private final VoiceCloneService voiceCloneService;
    private final ModelConfigService modelConfigService;

    @GetMapping
    @Operation(summary = "Page query voice resources")
    @Parameters({
            @Parameter(name = Constant.PAGE, description = "Current page number, starts from 1", required = true),
            @Parameter(name = Constant.LIMIT, description = "Records per page", required = true)
    })
    @RequiresPermissions("sys:role:superAdmin")
    public Result<PageData<VoiceCloneResponseDTO>> page(
            @Parameter(hidden = true) @RequestParam Map<String, Object> params) {
        ValidatorUtils.validateEntity(params);
        PageData<VoiceCloneResponseDTO> page = voiceCloneService.pageWithNames(params);
        return new Result<PageData<VoiceCloneResponseDTO>>().ok(page);
    }

    @GetMapping("{id}")
    @Operation(summary = "Get voice resource details")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<VoiceCloneResponseDTO> get(@PathVariable("id") String id) {
        VoiceCloneResponseDTO data = voiceCloneService.getByIdWithNames(id);
        return new Result<VoiceCloneResponseDTO>().ok(data);
    }

    @PostMapping
    @Operation(summary = "Add voice resource")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<Void> save(@RequestBody VoiceCloneDTO dto) {
        if (dto == null) {
            return new Result<Void>().error(ErrorCode.VOICE_RESOURCE_INFO_EMPTY);
        }
        if (dto.getModelId() == null || dto.getModelId().isEmpty()) {
            return new Result<Void>().error(ErrorCode.VOICE_RESOURCE_PLATFORM_NAME_EMPTY);
        }
        if (dto.getVoiceIds() == null || dto.getVoiceIds().isEmpty()) {
            return new Result<Void>().error(ErrorCode.VOICE_RESOURCE_ID_EMPTY);
        }
        if (dto.getUserId() == null) {
            return new Result<Void>().error(ErrorCode.VOICE_RESOURCE_ACCOUNT_EMPTY);
        }
        try {
            voiceCloneService.save(dto);
            return new Result<Void>();
        } catch (tbot.common.exception.RenException e) {
            return new Result<Void>().error(e.getCode(), e.getMsg());
        } catch (RuntimeException e) {
            return new Result<Void>().error(ErrorCode.ADD_DATA_FAILED, e.getMessage());
        }
    }

    @DeleteMapping("/{id}")
    @Operation(summary = "Delete voice resource")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<Void> delete(@PathVariable("id") String[] ids) {
        if (ids == null || ids.length == 0) {
            return new Result<Void>().error(ErrorCode.VOICE_RESOURCE_DELETE_ID_EMPTY);
        }
        voiceCloneService.delete(ids);
        return new Result<Void>();
    }

    @GetMapping("/user/{userId}")
    @Operation(summary = "Get voice resource list by user ID")
    @RequiresPermissions("sys:role:normal")
    public Result<List<VoiceCloneResponseDTO>> getByUserId(@PathVariable("userId") Long userId) {
        List<VoiceCloneResponseDTO> list = voiceCloneService.getByUserIdWithNames(userId);
        return new Result<List<VoiceCloneResponseDTO>>().ok(list);
    }

    @GetMapping("/ttsPlatforms")
    @Operation(summary = "Get TTS platform list")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<List<Map<String, Object>>> getTtsPlatformList() {
        List<Map<String, Object>> list = modelConfigService.getTtsPlatformList();
        return new Result<List<Map<String, Object>>>().ok(list);
    }
}
