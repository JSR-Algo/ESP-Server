package tbot.modules.correctword.dao;

import java.util.List;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import tbot.common.dao.BaseDao;
import tbot.modules.correctword.entity.CorrectWordItemEntity;

@Mapper
public interface CorrectWordItemDao extends BaseDao<CorrectWordItemEntity> {

    int batchInsert(@Param("list") List<CorrectWordItemEntity> items);
}
