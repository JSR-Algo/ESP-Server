package tbot.modules.sys.service;

import java.util.List;
import java.util.Map;

import tbot.common.page.PageData;
import tbot.common.service.BaseService;
import tbot.modules.sys.dto.SysParamsDTO;
import tbot.modules.sys.entity.SysParamsEntity;

/**
 * Parameter management
 */
public interface SysParamsService extends BaseService<SysParamsEntity> {

    PageData<SysParamsDTO> page(Map<String, Object> params);

    List<SysParamsDTO> list(Map<String, Object> params);

    SysParamsDTO get(Long id);

    void save(SysParamsDTO dto);

    void update(SysParamsDTO dto);

    void delete(String[] ids);

    /**
     * Get parameter's value by parameter codevaluevalue
     *
     * @param paramCode Parameter code
     * @param fromCache Whether get from cache
     */
    String getValue(String paramCode, Boolean fromCache);

    /**
     * Get by parameter codevalueofObjectObject
     *
     * @param paramCode Parameter code
     * @param clazz     ObjectObject
     */
    <T> T getValueObject(String paramCode, Class<T> clazz);

    /**
     * Update by parameter codevalue
     *
     * @param paramCode  Parameter code
     * @param paramValue Parameter value
     */
    int updateValueByCode(String paramCode, String paramValue);

    /**
     * Init server key
     */
    void initServerSecret();
}
