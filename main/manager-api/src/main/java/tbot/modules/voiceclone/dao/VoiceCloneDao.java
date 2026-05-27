package tbot.modules.voiceclone.dao;

import java.util.List;

import org.apache.ibatis.annotations.Mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;

import tbot.modules.model.dto.VoiceDTO;
import tbot.modules.voiceclone.entity.VoiceCloneEntity;

/**
 * Voice Cloning
 */
@Mapper
public interface VoiceCloneDao extends BaseMapper<VoiceCloneEntity> {
    /**
     * Get user successfully trained voice list
     * 
     * @param modelId ModelID
     * @param userId  UserID
     * @return Trained successful voice list
     */
    List<VoiceDTO> getTrainSuccess(String modelId, Long userId);

}
