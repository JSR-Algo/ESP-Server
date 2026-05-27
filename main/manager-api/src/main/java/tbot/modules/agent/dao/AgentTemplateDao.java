package tbot.modules.agent.dao;

import org.apache.ibatis.annotations.Mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;

import tbot.modules.agent.entity.AgentTemplateEntity;

/**
 * @author chenerlei
 * @description For Table [ai_agent_template(Agent config template table)Database operation for ]Mapper
 * @createDate 2025-03-22 11:48:18
 */
@Mapper
public interface AgentTemplateDao extends BaseMapper<AgentTemplateEntity> {

}
