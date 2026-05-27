package tbot.modules.correctword.dao;

import org.apache.ibatis.annotations.Mapper;

import tbot.common.dao.BaseDao;
import tbot.modules.correctword.entity.CorrectWordFileEntity;

@Mapper
public interface CorrectWordFileDao extends BaseDao<CorrectWordFileEntity> {
}
