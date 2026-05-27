package tbot.modules.agent.service.biz.impl;

import java.util.Base64;
import java.util.Date;
import java.util.Objects;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import tbot.common.constant.Constant;
import tbot.common.redis.RedisKeys;
import tbot.common.redis.RedisUtils;
import tbot.modules.agent.dto.AgentChatHistoryReportDTO;
import tbot.modules.agent.entity.AgentChatHistoryEntity;
import tbot.modules.agent.entity.AgentEntity;
import tbot.modules.agent.service.AgentChatAudioService;
import tbot.modules.agent.service.AgentChatHistoryService;
import tbot.modules.agent.service.AgentChatSummaryService;
import tbot.modules.agent.service.AgentService;
import tbot.modules.agent.service.biz.AgentChatHistoryBizService;
import tbot.modules.device.entity.DeviceEntity;
import tbot.modules.device.service.DeviceService;

/**
 * {@link AgentChatHistoryBizService} impl
 *
 * @author Goody
 * @version 1.0, 2025/4/30
 * @since 1.0.0
 */
@Service
@Slf4j
@RequiredArgsConstructor
public class AgentChatHistoryBizServiceImpl implements AgentChatHistoryBizService {
    private final AgentService agentService;
    private final AgentChatHistoryService agentChatHistoryService;
    private final AgentChatAudioService agentChatAudioService;
    private final AgentChatSummaryService agentChatSummaryService;
    private final RedisUtils redisUtils;
    private final DeviceService deviceService;

    /**
     * Handle chat history report, including file upload and relatedInfoRecord
     *
     * @param report Include data needed for chat reportInfoInput object
     * @return Upload result,trueIndicates success,falseIndicate Failure
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public Boolean report(AgentChatHistoryReportDTO report) {
        String macAddress = report.getMacAddress();
        Byte chatType = report.getChatType();
        Long reportTimeMillis = null != report.getReportTime() ? report.getReportTime()
                : System.currentTimeMillis();
        log.info("TBOT device chat report request: macAddress={}, type={} reportTime={}", macAddress, chatType, reportTimeMillis);

        // Based onDevice MAC addressQuery corresponding default agent, determine whether report needed
        AgentEntity agentEntity = agentService.getDefaultAgentByMacAddress(macAddress);
        if (agentEntity == null) {
            return Boolean.FALSE;
        }

        Integer chatHistoryConf = agentEntity.getChatHistoryConf();
        String agentId = agentEntity.getId();

        if (Objects.equals(chatHistoryConf, Constant.ChatHistoryConfEnum.RECORD_TEXT.getCode())) {
            saveChatText(report, agentId, macAddress, null, reportTimeMillis);
        } else if (Objects.equals(chatHistoryConf, Constant.ChatHistoryConfEnum.RECORD_TEXT_AUDIO.getCode())) {
            String audioId = saveChatAudio(report);
            saveChatText(report, agentId, macAddress, audioId, reportTimeMillis);
        }

        // Update device last conversation time
        redisUtils.set(RedisKeys.getAgentDeviceLastConnectedAtById(agentId), new Date());

        // Update DeviceLast connection time
        DeviceEntity device = deviceService.getDeviceByMacAddress(macAddress);
        if (device != null) {
            deviceService.updateDeviceConnectionInfo(agentId, device.getId(), null);
        } else {
            log.warn("During chat history reporting, device with mac address {} not found", macAddress);
        }

        return Boolean.TRUE;
    }

    /**
     * base64Decodereport.getOpusDataBase64(),Store inai_agent_chat_audiotable
     */
    private String saveChatAudio(AgentChatHistoryReportDTO report) {
        String audioId = null;

        if (report.getAudioBase64() != null && !report.getAudioBase64().isEmpty()) {
            try {
                byte[] audioData = Base64.getDecoder().decode(report.getAudioBase64());
                audioId = agentChatAudioService.saveAudio(audioData);
                log.info("Audio data saved successfully, audioId={}", audioId);
            } catch (Exception e) {
                log.error("Audio data save failed", e);
                return null;
            }
        }
        return audioId;
    }

    /**
     * Assemble report data
     */
    private void saveChatText(AgentChatHistoryReportDTO report, String agentId, String macAddress, String audioId,
            Long reportTime) {
        // Build chat history entity
        AgentChatHistoryEntity entity = AgentChatHistoryEntity.builder()
                .macAddress(macAddress)
                .agentId(agentId)
                .sessionId(report.getSessionId())
                .chatType(report.getChatType())
                .content(report.getContent())
                .audioId(audioId)
                .createdAt(new Date(reportTime))
                // NOTE(haotian): 2025/5/26 updateAtCan omit, focus iscreateAt, and this can show report delay
                .build();

        // SaveData
        agentChatHistoryService.save(entity);

        log.info("Device {} reported successfully for agent {}", macAddress, agentId);
    }
}
