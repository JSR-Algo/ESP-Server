package tbot.modules.sys.service;

import java.util.List;
import java.util.Map;

import tbot.common.page.PageData;
import tbot.common.service.BaseService;
import tbot.modules.sys.dto.SysDictTypeDTO;
import tbot.modules.sys.entity.SysDictTypeEntity;
import tbot.modules.sys.vo.SysDictTypeVO;

/**
 * Data Dictionary
 */
public interface SysDictTypeService extends BaseService<SysDictTypeEntity> {

    /**
     * Paginated query dictionary type info
     *
     * @param params Query parameters, include pagination info and query conditions
     * @return Return paged dictionary type data
     */
    PageData<SysDictTypeVO> page(Map<String, Object> params);

    /**
     * Based onIDGet dict type info
     *
     * @param id Dictionary TypeID
     * @return Return dict type object
     */
    SysDictTypeVO get(Long id);

    /**
     * Save dict type info
     *
     * @param dto Dictionary type data transfer object
     */
    void save(SysDictTypeDTO dto);

    /**
     * Update dict type info
     *
     * @param dto Dictionary type data transfer object
     */
    void update(SysDictTypeDTO dto);

    /**
     * Delete dict type info
     *
     * @param ids Dict type to deleteIDArray
     */
    void delete(Long[] ids);

    /**
     * List all dictionary type info
     *
     * @return Return dict type list
     */
    List<SysDictTypeVO> list(Map<String, Object> params);
}