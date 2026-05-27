package tbot.modules.sys.dao;

import java.util.List;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import tbot.common.dao.BaseDao;
import tbot.modules.sys.entity.SysParamsEntity;

/**
 * Parameter management
 */
@Mapper
public interface SysParamsDao extends BaseDao<SysParamsEntity> {
    /**
     * Query by parameter codevalue
     *
     * @param paramCode Parameter code
     * @return Parameter value
     */
    String getValueByCode(String paramCode);

    /**
     * Get parameter code list
     *
     * @param ids ids
     * @return Return parameter code list
     */
    List<String> getParamCodeList(String[] ids);

    /**
     * Update by parameter codevalue
     *
     * @param paramCode  Parameter code
     * @param paramValue Parameter value
     */
    int updateValueByCode(@Param("paramCode") String paramCode, @Param("paramValue") String paramValue);
}
