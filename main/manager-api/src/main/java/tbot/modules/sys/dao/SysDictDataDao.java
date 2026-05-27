package tbot.modules.sys.dao;

import java.util.List;

import org.apache.ibatis.annotations.Mapper;

import org.apache.ibatis.annotations.Param;
import tbot.common.dao.BaseDao;
import tbot.modules.sys.entity.SysDictDataEntity;
import tbot.modules.sys.vo.SysDictDataItem;

/**
 * Dictionary Data
 */
@Mapper
public interface SysDictDataDao extends BaseDao<SysDictDataEntity> {

    List<SysDictDataItem> getDictDataByType(String dictType);

    /**
     * Based on dict typeIDGet dict type code
     * 
     * @param dictTypeId Dictionary TypeID
     * @return Dictionary type code
     */
    String getTypeByTypeId(Long dictTypeId);

    /**
     * Based on dict dataIDGet dictionary type code collection from collection
     */
    List<String> getDictTypesByIdList(@Param("dictDataIdList") List<Long> dictDataIdList);
}
