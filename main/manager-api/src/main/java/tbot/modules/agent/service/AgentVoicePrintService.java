package tbot.modules.agent.service;

import java.util.List;

import tbot.modules.agent.dto.AgentVoicePrintSaveDTO;
import tbot.modules.agent.dto.AgentVoicePrintUpdateDTO;
import tbot.modules.agent.vo.AgentVoicePrintVO;

/**
 * Agent voiceprint handlingservice
 *
 * @author zjy
 */
public interface AgentVoicePrintService {
    /**
     * Add new voiceprint for agent
     *
     * @param dto Save agent voiceprint data
     * @return T:Success F: failure
     */
    boolean insert(AgentVoicePrintSaveDTO dto);

    /**
     * Delete specified voiceprint of agent
     *
     * @param userId       Current logged-in userid
     * @param voicePrintId Voiceprintid
     * @return Successful T:Success F: failure
     */
    boolean delete(Long userId, String voicePrintId);

    /**
     * Get all voiceprint data of specified agent
     *
     * @param userId  Current logged-in userid
     * @param agentId Agentid
     * @return Voiceprint data collection
     */
    List<AgentVoicePrintVO> list(Long userId, String agentId);

    /**
     * Update specified voiceprint data of agent
     *
     * @param userId Current logged-in userid
     * @param dto    Modified voiceprint data
     * @return Successful T:Success F: failure
     */
    boolean update(Long userId, AgentVoicePrintUpdateDTO dto);

}
