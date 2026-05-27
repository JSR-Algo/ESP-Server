package tbot.modules.voiceclone.controller;

import java.util.Map;
import java.util.UUID;

import org.apache.commons.lang3.StringUtils;
import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.Parameters;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.page.PageData;
import tbot.common.redis.RedisKeys;
import tbot.common.redis.RedisUtils;
import tbot.common.user.UserDetail;
import tbot.common.utils.Result;
import tbot.common.validator.ValidatorUtils;
import tbot.modules.security.user.SecurityUser;
import tbot.modules.voiceclone.dto.VoiceCloneResponseDTO;
import tbot.modules.voiceclone.entity.VoiceCloneEntity;
import tbot.modules.voiceclone.service.VoiceCloneService;

@Tag(name = "Voice resource management", description = "Voice resource activation APIs")
@Slf4j
@RestController
@RequiredArgsConstructor
@RequestMapping("/voiceClone")
public class VoiceCloneController {

    private final VoiceCloneService voiceCloneService;
    private final RedisUtils redisUtils;

    @GetMapping
    @Operation(summary = "Page query voice resources")
    @Parameters({
            @Parameter(name = Constant.PAGE, description = "Current page number, starts from 1", required = true),
            @Parameter(name = Constant.LIMIT, description = "Records per page", required = true)
    })
    @RequiresPermissions("sys:role:normal")
    public Result<PageData<VoiceCloneResponseDTO>> page(
            @Parameter(hidden = true) @RequestParam Map<String, Object> params) {
        ValidatorUtils.validateEntity(params);
        UserDetail user = SecurityUser.getUser();
        params.put("userId", user.getId().toString());
        PageData<VoiceCloneResponseDTO> page = voiceCloneService.pageWithNames(params);
        return new Result<PageData<VoiceCloneResponseDTO>>().ok(page);
    }

    @PostMapping("/upload")
    @Operation(summary = "Upload audio for voice cloning")
    @Parameters({
            @Parameter(name = "id", description = "Voice clone record ID", required = true),
            @Parameter(name = "voiceFile", description = "Audio file", required = true)
    })
    @RequiresPermissions("sys:role:normal")
    public Result<String> uploadVoice(
            @RequestParam("id") String id,
            @RequestParam("voiceFile") MultipartFile voiceFile) {
        try {
            // Verify File
            if (voiceFile == null || voiceFile.isEmpty()) {
                return new Result<String>().error(ErrorCode.VOICE_CLONE_AUDIO_EMPTY);
            }

            // VerifyFile type
            String contentType = voiceFile.getContentType();
            if (contentType == null || !contentType.startsWith("audio/")) {
                return new Result<String>().error(ErrorCode.VOICE_CLONE_NOT_AUDIO_FILE);
            }

            // Strengthen VerificationFile extension
            String originalFilename = voiceFile.getOriginalFilename();
            String extension = originalFilename.substring(originalFilename.lastIndexOf(".")).toLowerCase();
            if (!extension.equals(".mp3") && !extension.equals(".wav")) {
                return new Result<String>().error("Only .mp3 and .wav files can be uploaded");
            }

            // VerifyFile size (Maximum10MB)
            if (voiceFile.getSize() > 10 * 1024 * 1024) {
                return new Result<String>().error(ErrorCode.VOICE_CLONE_AUDIO_TOO_LARGE);
            }
            // Check Permission
            checkPermission(id);
            // Call service layer handle
            voiceCloneService.uploadVoice(id, voiceFile);

            return new Result<String>();
        } catch (Exception e) {
            return new Result<String>().error(ErrorCode.VOICE_CLONE_UPLOAD_FAILED, e.getMessage());
        }
    }

    @PostMapping("/updateName")
    @Operation(summary = "Update voice clone name")
    @RequiresPermissions("sys:role:normal")
    public Result<String> updateName(@RequestBody Map<String, String> params) {
        try {
            String id = params.get("id");
            String name = params.get("name");

            if (id == null || id.isEmpty()) {
                return new Result<String>().error(ErrorCode.IDENTIFIER_NOT_NULL);
            }
            if (name == null || name.isEmpty()) {
                return new Result<String>().error(ErrorCode.VOICE_CLONE_NAME_NOT_NULL);
            }
            // Check Permission
            checkPermission(id);

            voiceCloneService.updateName(id, name);
            redisUtils.delete(RedisKeys.getTimbreNameById(id));
            return new Result<String>();
        } catch (Exception e) {
            return new Result<String>().error(ErrorCode.UPDATE_DATA_FAILED, e.getMessage());
        }
    }

    @PostMapping("/audio/{id}")
    @Operation(summary = "Get audio download ID")
    @RequiresPermissions("sys:role:normal")
    public Result<String> getAudioId(@PathVariable("id") String id) {
        // Check Permission
        checkPermission(id);
        byte[] audioData = voiceCloneService.getVoiceData(id);
        if (audioData == null) {
            return new Result<String>().error(ErrorCode.VOICE_CLONE_AUDIO_NOT_FOUND);
        }
        String uuid = UUID.randomUUID().toString();
        redisUtils.set(RedisKeys.getVoiceCloneAudioIdKey(uuid), id);
        return new Result<String>().ok(uuid);
    }

    @GetMapping("/play/{uuid}")
    @Operation(summary = "Play audio")
    public void playVoice(@PathVariable("uuid") String uuid, HttpServletResponse response) {
        try {
            String id = (String) redisUtils.get(RedisKeys.getVoiceCloneAudioIdKey(uuid));
            redisUtils.delete(RedisKeys.getVoiceCloneAudioIdKey(uuid));
            if (StringUtils.isBlank(id)) {
                response.setStatus(HttpServletResponse.SC_NOT_FOUND);
                return;
            }
            // Get audio data
            byte[] voiceData = voiceCloneService.getVoiceData(id);

            if (voiceData == null || voiceData.length == 0) {
                response.setStatus(HttpServletResponse.SC_NOT_FOUND);
                return;
            }

            // SetResponsehead
            response.setContentType("audio/wav");
            response.setContentLength(voiceData.length);
            response.setHeader("Content-Disposition", "inline; filename=voice.wav");

            // Write audio data
            response.getOutputStream().write(voiceData);
            response.getOutputStream().flush();
        } catch (Exception e) {
            log.error("Play audio failed", e);
            response.setStatus(HttpServletResponse.SC_INTERNAL_SERVER_ERROR);
        }
    }

    @PostMapping("/cloneAudio")
    @Operation(summary = "Cloned audio")
    @RequiresPermissions("sys:role:normal")
    public Result<String> cloneAudio(@RequestBody Map<String, String> params) {
        String cloneId = params.get("cloneId");
        checkPermission(cloneId);
        // Call service layer for voice clone training
        voiceCloneService.cloneAudio(cloneId);
        return new Result<String>();
    }

    private void checkPermission(String id) {
        VoiceCloneEntity voiceClone = voiceCloneService.selectById(id);
        if (voiceClone == null) {
            throw new RenException(ErrorCode.VOICE_CLONE_RECORD_NOT_EXIST);
        }
        if (!voiceClone.getUserId().equals(SecurityUser.getUser().getId())) {
            throw new RenException(ErrorCode.VOICE_RESOURCE_NO_PERMISSION);
        }
    }
}
