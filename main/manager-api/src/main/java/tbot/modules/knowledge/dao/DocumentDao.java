package tbot.modules.knowledge.dao;

import org.apache.ibatis.annotations.Mapper;
import tbot.common.dao.BaseDao;
import tbot.modules.knowledge.entity.DocumentEntity;

/**
 * Document DAO
 */
@Mapper
public interface DocumentDao extends BaseDao<DocumentEntity> {
}
