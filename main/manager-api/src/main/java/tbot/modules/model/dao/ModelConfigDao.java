package tbot.modules.model.dao;

import java.util.List;
import java.util.Map;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import tbot.common.dao.BaseDao;
import tbot.modules.model.entity.ModelConfigEntity;

@Mapper
public interface ModelConfigDao extends BaseDao<ModelConfigEntity> {

    /**
     * get model_code list
     */
    List<String> getModelCodeList(@Param("modelType") String modelType, @Param("modelName") String modelName);

    /**
     * Get matchingTTSPlatform list(idandmodelName)
     */
    List<Map<String, Object>> getTtsPlatformList();
}
