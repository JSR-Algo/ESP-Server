package tbot.modules.sys.service;

import java.util.List;
import java.util.Map;

import tbot.common.page.PageData;
import tbot.common.service.BaseService;
import tbot.modules.sys.dto.SysDictDataDTO;
import tbot.modules.sys.entity.SysDictDataEntity;
import tbot.modules.sys.vo.SysDictDataItem;
import tbot.modules.sys.vo.SysDictDataVO;

/**
 * Data Dictionary
 */
public interface SysDictDataService extends BaseService<SysDictDataEntity> {

    /**
     * Paginated query data dictionary info
     *
     * @param params Query parameters, include pagination info and query conditions
     * @return Return paginated query result of data dictionary
     */
    PageData<SysDictDataVO> page(Map<String, Object> params);

    /**
     * Based onIDGet data dict entity
     *
     * @param id Unique identifier of data dictionary entity
     * @return Return details of data dictionary entity
     */
    SysDictDataVO get(Long id);

    /**
     * Save new data dictionary item
     *
     * @param dto Save data transfer object of data dictionary item
     */
    void save(SysDictDataDTO dto);

    /**
     * Update data dictionary item
     *
     * @param dto Update data transfer object of data dictionary item
     */
    void update(SysDictDataDTO dto);

    /**
     * Delete data dictionary item
     *
     * @param ids Data dictionary item to delete'sIDArray
     */
    void delete(Long[] ids);

    /**
     * Based on dict typeIDDelete corresponding dictionary data
     *
     * @param dictTypeId Dictionary TypeID
     */
    void deleteByTypeId(Long dictTypeId);

    /**
     * Get dictionary data list by dictionary type
     *
     * @param dictType Dictionary Type
     * @return Return dict data list
     */
    List<SysDictDataItem> getDictDataByType(String dictType);

}