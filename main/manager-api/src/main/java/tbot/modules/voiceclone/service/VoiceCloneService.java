package tbot.modules.voiceclone.service;

import java.util.List;
import java.util.Map;

import org.springframework.web.multipart.MultipartFile;

import tbot.common.page.PageData;
import tbot.common.service.BaseService;
import tbot.modules.voiceclone.dto.VoiceCloneDTO;
import tbot.modules.voiceclone.dto.VoiceCloneResponseDTO;
import tbot.modules.voiceclone.entity.VoiceCloneEntity;

/**
 * Voice clone management
 */
public interface VoiceCloneService extends BaseService<VoiceCloneEntity> {

    /**
     * Paged Query
     */
    PageData<VoiceCloneEntity> page(Map<String, Object> params);

    /**
     * Save voice clone
     */
    void save(VoiceCloneDTO dto);

    /**
     * Batch delete
     */
    void delete(String[] ids);

    /**
     * According to UserIDQuery voice clone list
     * 
     * @param userId UserID
     * @return Voice clone list
     */
    List<VoiceCloneEntity> getByUserId(Long userId);

    /**
     * Paginated query voice clone list with model name and user name
     */
    PageData<VoiceCloneResponseDTO> pageWithNames(Map<String, Object> params);

    /**
     * Based onIDQuery voice clone info with model name and user name
     */
    VoiceCloneResponseDTO getByIdWithNames(String id);

    /**
     * According to UserIDQuery voice clone list with model names
     */
    List<VoiceCloneResponseDTO> getByUserIdWithNames(Long userId);

    /**
     * Upload audio file
     */
    void uploadVoice(String id, MultipartFile voiceFile) throws Exception;

    /**
     * Update voice clone name
     */
    void updateName(String id, String name);

    /**
     * Get audio data
     */
    byte[] getVoiceData(String id);

    /**
     * Clone audio, call Volcengine for voice cloning training
     * 
     * @param cloneId Voice clone recordID
     */
    void cloneAudio(String cloneId);
}
