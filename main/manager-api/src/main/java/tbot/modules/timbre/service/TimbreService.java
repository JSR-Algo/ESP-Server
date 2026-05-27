package tbot.modules.timbre.service;

import java.util.List;

import tbot.common.page.PageData;
import tbot.common.service.BaseService;
import tbot.modules.model.dto.VoiceDTO;
import tbot.modules.timbre.dto.TimbreDataDTO;
import tbot.modules.timbre.dto.TimbrePageDTO;
import tbot.modules.timbre.entity.TimbreEntity;
import tbot.modules.timbre.vo.TimbreDetailsVO;

/**
 * Voice business layer definition
 * 
 * @author zjy
 * @since 2025-3-21
 */
public interface TimbreService extends BaseService<TimbreEntity> {
    /**
     * Page get specified voicettsVoice under
     * 
     * @param dto Paged search parameters
     * @return Voice list paged data
     */
    PageData<TimbreDetailsVO> page(TimbrePageDTO dto);

    /**
     * Get specified voiceidDetails info
     * 
     * @param timbreId Voice tableid
     * @return Voice Info
     */
    TimbreDetailsVO get(String timbreId);

    /**
     * Save voice info
     * 
     * @param dto Need save data
     */
    void save(TimbreDataDTO dto);

    /**
     * Save voice info
     * 
     * @param timbreId Need modifyid
     * @param dto      Data to modify
     */
    void update(String timbreId, TimbreDataDTO dto);

    /**
     * Batch delete voices
     * 
     * @param ids Voice to deleteidList
     */
    void delete(String[] ids);

    List<VoiceDTO> getVoiceNames(String ttsModelId, String voiceName);

    /**
     * Based onIDGet voice name
     * 
     * @param id VoiceID
     * @return Voice name
     */
    String getTimbreNameById(String id);

    /**
     * Get voice info by voice code
     * 
     * @param ttsModelId Voice modelID
     * @param voiceCode  Voice code
     * @return Voice Info
     */
    VoiceDTO getByVoiceCode(String ttsModelId, String voiceCode);
}