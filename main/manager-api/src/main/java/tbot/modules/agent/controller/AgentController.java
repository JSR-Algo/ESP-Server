package tbot.modules.agent.controller;

import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.apache.commons.lang3.StringUtils;
import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
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

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.Parameters;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.AllArgsConstructor;
import tbot.common.constant.Constant;
import tbot.common.page.PageData;
import tbot.common.redis.RedisKeys;
import tbot.common.redis.RedisUtils;
import tbot.common.user.UserDetail;
import tbot.common.utils.Result;
import tbot.common.utils.ResultUtils;
import tbot.modules.agent.dto.AgentChatHistoryDTO;
import tbot.modules.agent.dto.AgentChatSessionDTO;
import tbot.modules.agent.dto.AgentCreateDTO;
import tbot.modules.agent.dto.AgentDTO;
import tbot.modules.agent.dto.AgentMemoryDTO;
import tbot.modules.agent.dto.AgentTtsVolumeDTO;
import tbot.modules.agent.dto.AgentUpdateDTO;
import tbot.modules.agent.entity.AgentEntity;
import tbot.modules.agent.entity.AgentTemplateEntity;
import tbot.modules.agent.dto.AgentTagDTO;
import tbot.modules.agent.entity.AgentTagEntity;
import tbot.modules.agent.service.AgentTagService;
import tbot.modules.agent.service.AgentChatAudioService;
import tbot.modules.agent.service.AgentChatHistoryService;
import tbot.modules.agent.service.AgentChatSummaryService;
import tbot.modules.agent.service.AgentContextProviderService;
import tbot.modules.agent.service.AgentPluginMappingService;
import tbot.modules.agent.service.AgentService;
import tbot.modules.agent.service.AgentTemplateService;
import tbot.modules.correctword.service.CorrectWordFileService;
import tbot.modules.agent.vo.AgentChatHistoryUserVO;
import tbot.modules.agent.vo.AgentInfoVO;
import tbot.modules.device.entity.DeviceEntity;
import tbot.modules.device.service.DeviceService;
import tbot.modules.security.user.SecurityUser;

@Tag(name = "Agent management")
@AllArgsConstructor
@RestController
@RequestMapping("/agent")
public class AgentController {
    private final AgentService agentService;
    private final AgentTemplateService agentTemplateService;
    private final DeviceService deviceService;
    private final AgentChatHistoryService agentChatHistoryService;
    private final AgentChatAudioService agentChatAudioService;
    private final AgentPluginMappingService agentPluginMappingService;
    private final AgentContextProviderService agentContextProviderService;
    private final AgentChatSummaryService agentChatSummaryService;
    private final RedisUtils redisUtils;
    private final AgentTagService agentTagService;
    private final CorrectWordFileService correctWordFileService;

    @GetMapping("/list")
    @Operation(summary = "Get user agent list")
    @RequiresPermissions("sys:role:normal")
    public Result<List<AgentDTO>> getUserAgents(
            @RequestParam(value = "keyword", required = false) String keyword,
            @RequestParam(value = "searchType", defaultValue = "name") String searchType) {
        UserDetail user = SecurityUser.getUser();

        // Call integrated directlygetUserAgentsMethod, no need distinguish search and normal query
        List<AgentDTO> agents = agentService.getUserAgents(user.getId(), keyword, searchType);
        return new Result<List<AgentDTO>>().ok(agents);
    }

    @GetMapping("/all")
    @Operation(summary = "Agent list (admin)")
    @RequiresPermissions("sys:role:superAdmin")
    @Parameters({
            @Parameter(name = Constant.PAGE, description = "Current page number, starts from 1", required = true),
            @Parameter(name = Constant.LIMIT, description = "Records per page", required = true),
    })
    public Result<PageData<AgentEntity>> adminAgentList(
            @Parameter(hidden = true) @RequestParam Map<String, Object> params) {
        PageData<AgentEntity> page = agentService.adminAgentList(params);
        return new Result<PageData<AgentEntity>>().ok(page);
    }

    @GetMapping("/{id}")
    @Operation(summary = "Get agent details")
    @RequiresPermissions("sys:role:normal")
    public Result<AgentInfoVO> getAgentById(@PathVariable("id") String id) {
        AgentInfoVO agent = agentService.getAgentById(id);
        return ResultUtils.success(agent);
    }

    @PostMapping
    @Operation(summary = "Create agent")
    @RequiresPermissions("sys:role:normal")
    public Result<String> save(@RequestBody @Valid AgentCreateDTO dto) {
        String agentId = agentService.createAgent(dto);
        return new Result<String>().ok(agentId);
    }

    @PutMapping("/saveMemory/{macAddress}")
    @Operation(summary = "Update agent by device id")
    public Result<Void> updateByDeviceId(@PathVariable String macAddress, @RequestBody @Valid AgentMemoryDTO dto) {
        DeviceEntity device = deviceService.getDeviceByMacAddress(macAddress);
        if (device == null) {
            return new Result<>();
        }
        AgentUpdateDTO agentUpdateDTO = new AgentUpdateDTO();
        agentUpdateDTO.setSummaryMemory(dto.getSummaryMemory());
        agentService.updateAgentById(device.getAgentId(), agentUpdateDTO);
        return new Result<>();
    }

    @PutMapping("/ttsVolume/{macAddress}")
    @Operation(summary = "Update agent TTS volume by device id")
    public Result<Void> updateTtsVolumeByDeviceId(@PathVariable String macAddress,
            @RequestBody @Valid AgentTtsVolumeDTO dto) {
        DeviceEntity device = deviceService.getDeviceByMacAddress(macAddress);
        if (device == null) {
            return new Result<>();
        }
        AgentUpdateDTO agentUpdateDTO = new AgentUpdateDTO();
        agentUpdateDTO.setTtsVolume(dto.getTtsVolume());
        agentService.updateAgentById(device.getAgentId(), agentUpdateDTO);
        return new Result<>();
    }

    @PostMapping("/chat-summary/{sessionId}/save")
    @Operation(summary = "Generate and save chat history summary by session ID (async execution)")
    public Result<Void> generateAndSaveChatSummary(@PathVariable String sessionId) {
        try {
            // Async execute summary generation task, return success immediatelyResponse
            new Thread(() -> {
                try {
                    agentChatSummaryService.generateAndSaveChatSummary(sessionId);
                    System.out.println("Execute session async " + sessionId + " chat history summary completed");
                } catch (Exception e) {
                    System.err.println("Execute session async " + sessionId + " chat history summary failed: " + e.getMessage());
                }
            }).start();

            // Return success immediatelyResponsedo not wait for summary generation complete
            return new Result<Void>().ok(null);
        } catch (Exception e) {
            return new Result<Void>().error("Start async summary generation task failed: " + e.getMessage());
        }
    }

    @PostMapping("/chat-title/{sessionId}/generate")
    @Operation(summary = "Generate chat title by session ID")
    public Result<Void> generateAndSaveChatTitle(@PathVariable String sessionId) {
        agentChatSummaryService.generateAndSaveChatTitle(sessionId);
        return new Result<Void>().ok(null);
    }

    @PutMapping("/{id}")
    @Operation(summary = "Update agent")
    @RequiresPermissions("sys:role:normal")
    public Result<Void> update(@PathVariable String id, @RequestBody @Valid AgentUpdateDTO dto) {
        agentService.updateAgentById(id, dto);
        return new Result<>();
    }

    @DeleteMapping("/{id}")
    @Operation(summary = "Delete agent")
    @RequiresPermissions("sys:role:normal")
    public Result<Void> delete(@PathVariable String id) {
        // firstDeleteAssociated device
        deviceService.deleteByAgentId(id);
        // DeleteAssociated chat history
        agentChatHistoryService.deleteByAgentId(id, true, true);
        // DeleteAssociated plugin
        agentPluginMappingService.deleteByAgentId(id);
        // DeleteAssociatedContext source config
        agentContextProviderService.deleteByAgentId(id);
        // DeleteAssociatedReplacement wordFile association record
        correctWordFileService.deleteMappingsByAgentId(id);
        // againDelete agent
        agentService.deleteById(id);
        return new Result<>();
    }

    @GetMapping("/template")
    @Operation(summary = "Agent template list")
    @RequiresPermissions("sys:role:normal")
    public Result<List<AgentTemplateEntity>> templateList() {
        List<AgentTemplateEntity> list = agentTemplateService
                .list(new QueryWrapper<AgentTemplateEntity>().orderByAsc("sort"));
        return new Result<List<AgentTemplateEntity>>().ok(list);
    }

    @GetMapping("/{id}/sessions")
    @Operation(summary = "Get agent session list")
    @RequiresPermissions("sys:role:normal")
    @Parameters({
            @Parameter(name = Constant.PAGE, description = "Current page number, starts from 1", required = true),
            @Parameter(name = Constant.LIMIT, description = "Records per page", required = true),
    })
    public Result<PageData<AgentChatSessionDTO>> getAgentSessions(
            @PathVariable("id") String id,
            @Parameter(hidden = true) @RequestParam Map<String, Object> params) {
        params.put("agentId", id);
        PageData<AgentChatSessionDTO> page = agentChatHistoryService.getSessionListByAgentId(params);
        return new Result<PageData<AgentChatSessionDTO>>().ok(page);
    }

    @GetMapping("/{id}/chat-history/{sessionId}")
    @Operation(summary = "Get agent chat records")
    @RequiresPermissions("sys:role:normal")
    public Result<List<AgentChatHistoryDTO>> getAgentChatHistory(
            @PathVariable("id") String id,
            @PathVariable("sessionId") String sessionId) {
        // Get current user
        UserDetail user = SecurityUser.getUser();

        // Check Permission
        if (!agentService.checkAgentPermission(id, user.getId())) {
            return new Result<List<AgentChatHistoryDTO>>().error("No permission to view this agent's chat records");
        }

        // Query chat records
        List<AgentChatHistoryDTO> result = agentChatHistoryService.getChatHistoryBySessionId(id, sessionId);
        return new Result<List<AgentChatHistoryDTO>>().ok(result);
    }

    @GetMapping("/{id}/chat-history/user")
    @Operation(summary = "Get agent chat records (user)")
    @RequiresPermissions("sys:role:normal")
    public Result<List<AgentChatHistoryUserVO>> getRecentlyFiftyByAgentId(
            @PathVariable("id") String id) {
        // Get current user
        UserDetail user = SecurityUser.getUser();

        // Check Permission
        if (!agentService.checkAgentPermission(id, user.getId())) {
            return new Result<List<AgentChatHistoryUserVO>>().error("No permission to view this agent's chat records");
        }

        // Query chat records
        List<AgentChatHistoryUserVO> data = agentChatHistoryService.getRecentlyFiftyByAgentId(id);
        return new Result<List<AgentChatHistoryUserVO>>().ok(data);
    }

    @GetMapping("/{id}/chat-history/audio")
    @Operation(summary = "Get audio content")
    @RequiresPermissions("sys:role:normal")
    public Result<String> getContentByAudioId(
            @PathVariable("id") String id) {
        // Query chat records
        String data = agentChatHistoryService.getContentByAudioId(id);
        return new Result<String>().ok(data);
    }

    @PostMapping("/audio/{audioId}")
    @Operation(summary = "Get audio download ID")
    @RequiresPermissions("sys:role:normal")
    public Result<String> getAudioId(@PathVariable("audioId") String audioId) {
        byte[] audioData = agentChatAudioService.getAudio(audioId);
        if (audioData == null) {
            return new Result<String>().error("Audio does not exist");
        }
        String uuid = UUID.randomUUID().toString();
        redisUtils.set(RedisKeys.getAgentAudioIdKey(uuid), audioId);
        return new Result<String>().ok(uuid);
    }

    @GetMapping("/play/{uuid}")
    @Operation(summary = "Play audio")
    public ResponseEntity<byte[]> playAudio(@PathVariable("uuid") String uuid) {

        String audioId = (String) redisUtils.get(RedisKeys.getAgentAudioIdKey(uuid));
        if (StringUtils.isBlank(audioId)) {
            return ResponseEntity.notFound().build();
        }

        byte[] audioData = agentChatAudioService.getAudio(audioId);
        if (audioData == null) {
            return ResponseEntity.notFound().build();
        }
        redisUtils.delete(RedisKeys.getAgentAudioIdKey(uuid));
        return ResponseEntity.ok()
                .contentType(MediaType.APPLICATION_OCTET_STREAM)
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"play.wav\"")
                .body(audioData);
    }

    @PostMapping("/tag")
    @Operation(summary = "Create tag")
    @RequiresPermissions("sys:role:normal")
    public Result<AgentTagEntity> createTag(@RequestBody Map<String, String> params) {
        String tagName = params.get("tagName");
        if (StringUtils.isBlank(tagName)) {
            return new Result<AgentTagEntity>().error("Tag name cannot be empty");
        }
        AgentTagEntity tag = agentTagService.saveTag(tagName);
        return new Result<AgentTagEntity>().ok(tag);
    }

    @GetMapping("/tag/list")
    @Operation(summary = "Get all tag list")
    @RequiresPermissions("sys:role:normal")
    public Result<List<AgentTagDTO>> getAllTags() {
        List<AgentTagDTO> tags = agentTagService.getAllTags();
        return new Result<List<AgentTagDTO>>().ok(tags);
    }

    @DeleteMapping("/tag/{id}")
    @Operation(summary = "Delete tag")
    @RequiresPermissions("sys:role:normal")
    public Result<Void> deleteTag(@PathVariable String id) {
        agentTagService.deleteTag(id);
        return new Result<Void>().ok(null);
    }

    @GetMapping("/{id}/tags")
    @Operation(summary = "Get agent tags")
    @RequiresPermissions("sys:role:normal")
    public Result<List<AgentTagDTO>> getAgentTags(@PathVariable String id) {
        List<AgentTagDTO> tags = agentTagService.getTagsByAgentId(id);
        return new Result<List<AgentTagDTO>>().ok(tags);
    }

    @PutMapping("/{id}/tags")
    @Operation(summary = "Save agent tags")
    @RequiresPermissions("sys:role:normal")
    public Result<Void> saveAgentTags(@PathVariable String id, @RequestBody Map<String, Object> params) {
        List<String> tagIds = (List<String>) params.get("tagIds");
        List<String> tagNames = (List<String>) params.get("tagNames");
        agentTagService.saveAgentTags(id, tagIds, tagNames);
        return new Result<Void>().ok(null);
    }

}
