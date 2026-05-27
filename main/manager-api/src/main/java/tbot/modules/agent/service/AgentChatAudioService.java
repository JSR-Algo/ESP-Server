package tbot.modules.agent.service;

import com.baomidou.mybatisplus.extension.service.IService;

import tbot.modules.agent.entity.AgentChatAudioEntity;

/**
 * Agent chat audio data table handlingservice
 *
 * @author Goody
 * @version 1.0, 2025/5/8
 * @since 1.0.0
 */
public interface AgentChatAudioService extends IService<AgentChatAudioEntity> {
    /**
     * Save audio data
     *
     * @param audioData Audio Data
     * @return AudioID
     */
    String saveAudio(byte[] audioData);

    /**
     * Get audio data
     *
     * @param audioId AudioID
     * @return Audio Data
     */
    byte[] getAudio(String audioId);
}
