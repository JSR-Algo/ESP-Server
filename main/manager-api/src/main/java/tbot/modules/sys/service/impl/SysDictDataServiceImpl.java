package tbot.modules.sys.service.impl;

import java.util.*;
import java.util.stream.Collectors;
import java.util.stream.Stream;

import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;

import lombok.AllArgsConstructor;
import tbot.common.exception.RenException;
import tbot.common.exception.ErrorCode;
import tbot.common.page.PageData;
import tbot.common.redis.RedisKeys;
import tbot.common.redis.RedisUtils;
import tbot.common.service.impl.BaseServiceImpl;
import tbot.common.utils.ConvertUtils;
import tbot.common.utils.ToolUtil;
import tbot.modules.sys.dao.SysDictDataDao;
import tbot.modules.sys.dao.SysUserDao;
import tbot.modules.sys.dto.SysDictDataDTO;
import tbot.modules.sys.entity.SysDictDataEntity;
import tbot.modules.sys.entity.SysUserEntity;
import tbot.modules.sys.service.SysDictDataService;
import tbot.modules.sys.vo.SysDictDataItem;
import tbot.modules.sys.vo.SysDictDataVO;

/**
 * Dictionary Type
 */
@Service
@AllArgsConstructor
public class SysDictDataServiceImpl extends BaseServiceImpl<SysDictDataDao, SysDictDataEntity>
        implements SysDictDataService {
    private final SysUserDao sysUserDao;
    private final RedisUtils redisUtils;

    @Override
    public PageData<SysDictDataVO> page(Map<String, Object> params) {
        IPage<SysDictDataEntity> page = baseDao.selectPage(getPage(params, "sort", true), getWrapper(params));

        PageData<SysDictDataVO> pageData = getPageData(page, SysDictDataVO.class);

        setUserName(pageData.getList());

        return pageData;
    }

    private QueryWrapper<SysDictDataEntity> getWrapper(Map<String, Object> params) {
        String dictTypeId = (String) params.get("dictTypeId");
        String dictLabel = (String) params.get("dictLabel");
        String dictValue = (String) params.get("dictValue");

        QueryWrapper<SysDictDataEntity> wrapper = new QueryWrapper<>();
        wrapper.eq("dict_type_id", Long.parseLong(dictTypeId));
        wrapper.like(StringUtils.isNotBlank(dictLabel), "dict_label", dictLabel);
        wrapper.like(StringUtils.isNotBlank(dictValue), "dict_value", dictValue);

        return wrapper;
    }

    @Override
    public SysDictDataVO get(Long id) {
        SysDictDataEntity entity = baseDao.selectById(id);

        return ConvertUtils.sourceToTarget(entity, SysDictDataVO.class);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void save(SysDictDataDTO dto) {
        // Labels of same dictionary type cannot be same
        checkDictValueUnique(dto.getDictTypeId(), dto.getDictValue(), null);

        SysDictDataEntity entity = ConvertUtils.sourceToTarget(dto, SysDictDataEntity.class);

        insert(entity);
        // DeleteRedisCache
        String dictType = baseDao.getTypeByTypeId(dto.getDictTypeId());
        redisUtils.delete(RedisKeys.getDictDataByTypeKey(dictType));
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void update(SysDictDataDTO dto) {
        // Labels of same dictionary type cannot be same
        checkDictValueUnique(dto.getDictTypeId(), dto.getDictValue(), String.valueOf(dto.getId()));

        SysDictDataEntity entity = ConvertUtils.sourceToTarget(dto, SysDictDataEntity.class);

        updateById(entity);
        // DeleteRedisCache
        String dictType = baseDao.getTypeByTypeId(dto.getDictTypeId());
        redisUtils.delete(RedisKeys.getDictDataByTypeKey(dictType));
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void delete(Long[] ids) {
        List<Long> idList = Arrays.asList(ids);
        if (ToolUtil.isNotEmpty(idList)) {
            //Batch deleteredisDictionary
            List<String> redisKeyList = new ArrayList<>();
            //Batch get dict types
            List<String> dictTypeList = Optional.ofNullable(baseDao.getDictTypesByIdList(idList)).orElseGet(ArrayList::new);
            dictTypeList.forEach(dictType -> redisKeyList.add(RedisKeys.getDictDataByTypeKey(dictType)));
            if (ToolUtil.isNotEmpty(redisKeyList)) {
                //Clear cache
                redisUtils.delete(redisKeyList);
            }
            //Batch delete dictionary data
            deleteBatchIds(Arrays.asList(ids));
        }
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void deleteByTypeId(Long dictTypeId) {
        LambdaQueryWrapper<SysDictDataEntity> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(SysDictDataEntity::getDictTypeId, dictTypeId);
        baseDao.delete(wrapper);
    }

    /**
     * Set username
     *
     * @param sysDictDataList Dict type collection
     */
    private void setUserName(List<SysDictDataVO> sysDictDataList) {
        // Collect all users ID
        Set<Long> userIds = sysDictDataList.stream().flatMap(vo -> Stream.of(vo.getCreator(), vo.getUpdater()))
                .filter(Objects::nonNull).collect(Collectors.toSet());

        // Set updater and creator names
        if (!userIds.isEmpty()) {
            List<SysUserEntity> sysUserEntities = sysUserDao.selectBatchIds(userIds);
            // putListConvert toMap,Map<Long, String>
            Map<Long, String> userNameMap = sysUserEntities.stream().collect(Collectors.toMap(SysUserEntity::getId,
                    SysUserEntity::getUsername, (existing, replacement) -> existing));

            sysDictDataList.forEach(vo -> {
                vo.setCreatorName(userNameMap.get(vo.getCreator()));
                vo.setUpdaterName(userNameMap.get(vo.getUpdater()));
            });
        }
    }

    private void checkDictValueUnique(Long dictTypeId, String dictValue, String excludeId) {
        LambdaQueryWrapper<SysDictDataEntity> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(SysDictDataEntity::getDictTypeId, dictTypeId).eq(SysDictDataEntity::getDictLabel, dictValue);
        if (StringUtils.isNotBlank(excludeId)) {
            queryWrapper.ne(SysDictDataEntity::getId, excludeId);
        }
        boolean exists = baseDao.exists(queryWrapper);
        if (exists) {
            throw new RenException(ErrorCode.DICT_LABEL_DUPLICATE);
        }
    }

    @Override
    public List<SysDictDataItem> getDictDataByType(String dictType) {
        if (StringUtils.isBlank(dictType)) {
            return null;
        }

        // First fromRedisGet Cache
        String key = RedisKeys.getDictDataByTypeKey(dictType);
        List<SysDictDataItem> cachedData = (List<SysDictDataItem>) redisUtils.get(key);
        if (cachedData != null) {
            return cachedData;
        }

        // If not in cache, get from database
        List<SysDictDataItem> data = baseDao.getDictDataByType(dictType);

        // Store inRedisCache
        if (data != null) {
            redisUtils.set(key, data);
        }

        return data;
    }
}