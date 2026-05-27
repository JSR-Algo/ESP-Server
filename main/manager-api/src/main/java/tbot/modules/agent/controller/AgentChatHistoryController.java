package tbot.modules.agent.controller;

import java.io.IOException;
import java.io.OutputStream;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.apache.commons.lang3.StringUtils;
import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.page.PageData;
import tbot.common.redis.RedisKeys;
import tbot.common.redis.RedisUtils;
import tbot.common.user.UserDetail;
import tbot.common.utils.DateUtils;
import tbot.common.utils.MessageUtils;
import tbot.common.utils.Result;
import tbot.modules.agent.dto.AgentChatHistoryDTO;
import tbot.modules.agent.dto.AgentChatHistoryReportDTO;
import tbot.modules.agent.dto.AgentChatSessionDTO;
import tbot.modules.agent.service.AgentChatHistoryService;
import tbot.modules.agent.service.AgentService;
import tbot.modules.agent.service.biz.AgentChatHistoryBizService;
import tbot.modules.security.user.SecurityUser;

@Tag(name = "Agent chat history management")
@RequiredArgsConstructor
@RestController
@RequestMapping("/agent/chat-history")
public class AgentChatHistoryController {
    private final AgentChatHistoryBizService agentChatHistoryBizService;
    private final AgentChatHistoryService agentChatHistoryService;
    private final AgentService agentService;
    private final RedisUtils redisUtils;

    /**
     * TBOT service chat report request
     * <p>
     * TBOT service chat report request, includeBase64Encoded audio data and relatedInfo.
     *
     * @param request Contain uploaded file and relatedInfoRequest object
     */
    @Operation(summary = "TBOT service chat report request")
    @PostMapping("/report")
    public Result<Boolean> uploadFile(@Valid @RequestBody AgentChatHistoryReportDTO request) {
        Boolean result = agentChatHistoryBizService.report(request);
        return new Result<Boolean>().ok(result);
    }

    /**
     * Get chat history download link
     * 
     * @param agentId   Agent ID
     * @param sessionId Session ID
     * @return UUIDAs download ID
     */
    @Operation(summary = "Get chat history download link")
    @RequiresPermissions("sys:role:normal")
    @PostMapping("/getDownloadUrl/{agentId}/{sessionId}")
    public Result<String> getDownloadUrl(@PathVariable("agentId") String agentId,
            @PathVariable("sessionId") String sessionId) {
        // Get current user
        UserDetail user = SecurityUser.getUser();
        // Check Permission
        if (!agentService.checkAgentPermission(agentId, user.getId())) {
            throw new RenException(ErrorCode.CHAT_HISTORY_NO_PERMISSION);
        }

        // GenerateUUID
        String uuid = UUID.randomUUID().toString();
        // StoreagentIdandsessionIdtoRedis, format isagentId:sessionId
        redisUtils.set(RedisKeys.getChatHistoryKey(uuid), agentId + ":" + sessionId);

        return new Result<String>().ok(uuid);
    }

    /**
     * Download this session chat records
     * 
     * @param uuid     Download ID
     * @param response HTTPResponse
     */
    @Operation(summary = "Download this session chat records")
    @GetMapping("/download/{uuid}/current")
    public void downloadCurrentSession(@PathVariable("uuid") String uuid,
            HttpServletResponse response) {
        // fromRedisGetagentIdandsessionId
        String agentSessionInfo = (String) redisUtils.get(RedisKeys.getChatHistoryKey(uuid));
        if (StringUtils.isBlank(agentSessionInfo)) {
            throw new RenException(ErrorCode.DOWNLOAD_LINK_EXPIRED);
        }

        try {
            // ParseagentIdandsessionId
            String[] parts = agentSessionInfo.split(":");
            if (parts.length != 2) {
                throw new RenException(ErrorCode.DOWNLOAD_LINK_INVALID);
            }
            String agentId = parts[0];
            String sessionId = parts[1];

            // Execute Download
            downloadChatHistory(agentId, List.of(sessionId), response);
        } finally {
            // After download completeDeleteUUIDPrevent abuse
            redisUtils.delete(RedisKeys.getChatHistoryKey(uuid));
        }
    }

    /**
     * Download this session and previous 20 chat records
     * 
     * @param uuid     Download ID
     * @param response HTTPResponse
     */
    @Operation(summary = "Download this session and previous 20 chat records")
    @GetMapping("/download/{uuid}/previous")
    public void downloadCurrentSessionWithPrevious(@PathVariable("uuid") String uuid,
            HttpServletResponse response) {
        // fromRedisGetagentIdandsessionId
        String agentSessionInfo = (String) redisUtils.get(RedisKeys.getChatHistoryKey(uuid));
        if (StringUtils.isBlank(agentSessionInfo)) {
            throw new RenException(ErrorCode.DOWNLOAD_LINK_EXPIRED);
        }

        try {
            // ParseagentIdandsessionId
            String[] parts = agentSessionInfo.split(":");
            if (parts.length != 2) {
                throw new RenException(ErrorCode.DOWNLOAD_LINK_INVALID);
            }
            String agentId = parts[0];
            String sessionId = parts[1];

            // Get all session list
            Map<String, Object> params = Map.of(
                    "agentId", agentId,
                    Constant.PAGE, 1,
                    Constant.LIMIT, 1000 // Get enough sessions
            );
            PageData<AgentChatSessionDTO> sessionPage = agentChatHistoryService.getSessionListByAgentId(params);
            List<AgentChatSessionDTO> allSessions = sessionPage.getList();

            // Find current session position in list
            int currentIndex = -1;
            for (int i = 0; i < allSessions.size(); i++) {
                if (allSessions.get(i).getSessionId().equals(sessionId)) {
                    currentIndex = i;
                    break;
                }
            }

            // If current session found, collect current session and previous20itemSession ID
            List<String> sessionIdsToDownload = new ArrayList<>();
            if (currentIndex != -1) {
                // From current session, take up to backward (later in array)20sessions (including current session)
                int endIndex = Math.min(allSessions.size() - 1, currentIndex + 20); // Ensure no out of bounds
                for (int i = currentIndex; i <= endIndex; i++) {
                    sessionIdsToDownload.add(allSessions.get(i).getSessionId());
                }
            }

            // If current session not found, download at least current session
            if (sessionIdsToDownload.isEmpty()) {
                sessionIdsToDownload.add(sessionId);
            }
            downloadChatHistory(agentId, sessionIdsToDownload, response);
        } finally {
            // After download completeDeleteUUIDPrevent abuse
            redisUtils.delete(RedisKeys.getChatHistoryKey(uuid));
        }
    }

    /**
     * Download chat records of specified session
     * 
     * @param agentId    Agent ID
     * @param sessionIds Session IDList
     * @param response   HTTPResponse
     */
    private void downloadChatHistory(String agentId, List<String> sessionIds, HttpServletResponse response) {
        try {
            // SetResponsehead
            response.setContentType("text/plain;charset=UTF-8");
            String fileName = URLEncoder.encode("history.txt", StandardCharsets.UTF_8.toString());
            response.setHeader("Content-Disposition", "attachment;filename=" + fileName);

            // Get chat records and writeResponsestream
            try (OutputStream out = response.getOutputStream()) {
                // Generate chat records for each session
                for (String sessionId : sessionIds) {
                    // Get all chat records for this session
                    List<AgentChatHistoryDTO> chatHistoryList = agentChatHistoryService
                            .getChatHistoryBySessionId(agentId, sessionId);

                    // Get first item from chat recordsMessageofCreation timeAs session time
                    if (!chatHistoryList.isEmpty()) {
                        Date firstMessageTime = chatHistoryList.get(0).getCreatedAt();
                        String sessionTimeStr = DateUtils.format(firstMessageTime, DateUtils.DATE_TIME_PATTERN);
                        out.write((sessionTimeStr + "\n").getBytes(StandardCharsets.UTF_8));
                    }

                    for (AgentChatHistoryDTO message : chatHistoryList) {
                        String role = message.getChatType() == 1 ? MessageUtils.getMessage(ErrorCode.CHAT_ROLE_USER)
                                : MessageUtils.getMessage(ErrorCode.CHAT_ROLE_AGENT);
                        String direction = message.getChatType() == 1 ? ">>" : "<<";
                        Date messageTime = message.getCreatedAt();
                        String messageTimeStr = DateUtils.format(messageTime, DateUtils.DATE_TIME_PATTERN);
                        String content = message.getContent();

                        String line = "[" + role + "]-[" + messageTimeStr + "]" + direction + ":" + content + "\n";
                        out.write(line.getBytes(StandardCharsets.UTF_8));
                    }

                    // Add blank line separator between sessions
                    if (sessionIds.indexOf(sessionId) < sessionIds.size() - 1) {
                        out.write("\n".getBytes(StandardCharsets.UTF_8));
                    }
                }

                out.flush();
            }
        } catch (IOException e) {
            e.printStackTrace();
        }
    }
}
