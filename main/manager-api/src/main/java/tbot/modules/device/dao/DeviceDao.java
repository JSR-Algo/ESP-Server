package tbot.modules.device.dao;

import java.util.Date;

import org.apache.ibatis.annotations.Mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;

import tbot.modules.device.entity.DeviceEntity;

@Mapper
public interface DeviceDao extends BaseMapper<DeviceEntity> {
    /**
     * Get last connection time of all devices for this agent
     * 
     * @param agentId Agentid
     * @return
     */
    Date getAllLastConnectedAtByAgentId(String agentId);

}