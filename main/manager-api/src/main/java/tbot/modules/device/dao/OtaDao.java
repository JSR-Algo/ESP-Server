package tbot.modules.device.dao;

import org.apache.ibatis.annotations.Mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;

import tbot.modules.device.entity.OtaEntity;

/**
 * OTAFirmware management
 */
@Mapper
public interface OtaDao extends BaseMapper<OtaEntity> {
    
}