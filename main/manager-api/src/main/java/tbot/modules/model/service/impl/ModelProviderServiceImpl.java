package tbot.modules.model.service.impl;

import java.util.Collection;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;

import cn.hutool.json.JSONArray;
import lombok.AllArgsConstructor;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.page.PageData;
import tbot.common.service.impl.BaseServiceImpl;
import tbot.common.user.UserDetail;
import tbot.common.utils.ConvertUtils;
import tbot.modules.knowledge.dao.KnowledgeBaseDao;
import tbot.modules.knowledge.entity.KnowledgeBaseEntity;
import tbot.modules.model.dao.ModelProviderDao;
import tbot.modules.model.dto.ModelProviderDTO;
import tbot.modules.model.entity.ModelProviderEntity;
import tbot.modules.model.service.ModelProviderService;
import tbot.modules.security.user.SecurityUser;

@Service
@AllArgsConstructor
public class ModelProviderServiceImpl extends BaseServiceImpl<ModelProviderDao, ModelProviderEntity>
        implements ModelProviderService {

    private final ModelProviderDao modelProviderDao;
    private final KnowledgeBaseDao knowledgeBaseDao;

    @Override
    public List<ModelProviderDTO> getPluginList() {
        // 1. Get plugin list
        LambdaQueryWrapper<ModelProviderEntity> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(ModelProviderEntity::getModelType, "Plugin");
        List<ModelProviderEntity> providerEntities = modelProviderDao.selectList(queryWrapper);
        List<ModelProviderDTO> resultList = ConvertUtils.sourceToTarget(providerEntities, ModelProviderDTO.class);

        // 2. Get current user's knowledge base list and append to result
        UserDetail userDetail = SecurityUser.getUser();
        if (userDetail != null && userDetail.getId() != null) {
            // Query current user's knowledge base
            LambdaQueryWrapper<KnowledgeBaseEntity> kbQueryWrapper = new LambdaQueryWrapper<>();
            kbQueryWrapper.eq(KnowledgeBaseEntity::getCreator, userDetail.getId());
            kbQueryWrapper.eq(KnowledgeBaseEntity::getStatus, 1); // Only getEnableStatusknowledge base
            List<KnowledgeBaseEntity> knowledgeBases = knowledgeBaseDao.selectList(kbQueryWrapper);

            // Convert knowledge base toModelProviderDTOFormat and add to result list
            for (KnowledgeBaseEntity kb : knowledgeBases) {
                ModelProviderDTO dto = new ModelProviderDTO();
                dto.setId(kb.getId());
                dto.setModelType("Rag");
                dto.setName("[Knowledge Base]" + kb.getName());
                dto.setProviderCode("ragflow"); // Assume AllRAGAll useragflow
                dto.setFields("[]");
                dto.setSort(0);
                dto.setCreateDate(kb.getCreatedAt());
                dto.setUpdateDate(kb.getUpdatedAt());
                dto.setCreator(0L);
                dto.setUpdater(0L);
                resultList.add(dto);
            }
        }

        return resultList;
    }

    @Override
    public ModelProviderDTO getById(String id) {
        ModelProviderEntity entity = modelProviderDao.selectById(id);
        return ConvertUtils.sourceToTarget(entity, ModelProviderDTO.class);
    }

    @Override
    public List<ModelProviderDTO> getPluginListByIds(Collection<String> ids) {
        LambdaQueryWrapper<ModelProviderEntity> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.in(ModelProviderEntity::getId, ids);
        queryWrapper.eq(ModelProviderEntity::getModelType, "Plugin");
        List<ModelProviderEntity> providerEntities = modelProviderDao.selectList(queryWrapper);
        return ConvertUtils.sourceToTarget(providerEntities, ModelProviderDTO.class);
    }

    @Override
    public List<ModelProviderDTO> getListByModelType(String modelType) {

        QueryWrapper<ModelProviderEntity> queryWrapper = new QueryWrapper<>();
        queryWrapper.eq("model_type", StringUtils.isBlank(modelType) ? "" : modelType);
        queryWrapper.orderByAsc("sort");
        List<ModelProviderEntity> providerEntities = modelProviderDao.selectList(queryWrapper);
        return ConvertUtils.sourceToTarget(providerEntities, ModelProviderDTO.class);
    }

    @Override
    public PageData<ModelProviderDTO> getListPage(ModelProviderDTO modelProviderDTO, String page, String limit) {

        Map<String, Object> params = new HashMap<String, Object>();
        params.put(Constant.PAGE, page);
        params.put(Constant.LIMIT, limit);
        params.put(Constant.ORDER_FIELD, List.of("model_type", "sort"));
        params.put(Constant.ORDER, "asc");

        IPage<ModelProviderEntity> pageParam = getPage(params, null, true);

        QueryWrapper<ModelProviderEntity> wrapper = new QueryWrapper<ModelProviderEntity>();

        if (StringUtils.isNotBlank(modelProviderDTO.getModelType())) {
            wrapper.eq("model_type", modelProviderDTO.getModelType());
        }

        if (StringUtils.isNotBlank(modelProviderDTO.getName())) {
            wrapper.and(w -> w.like("name", modelProviderDTO.getName())
                    .or()
                    .like("provider_code", modelProviderDTO.getName()));
        }
        return getPageData(modelProviderDao.selectPage(pageParam, wrapper), ModelProviderDTO.class);
    }

    @Override
    public ModelProviderDTO add(ModelProviderDTO modelProviderDTO) {
        UserDetail user = SecurityUser.getUser();
        modelProviderDTO.setCreator(user.getId());
        modelProviderDTO.setUpdater(user.getId());
        modelProviderDTO.setCreateDate(new Date());
        modelProviderDTO.setUpdateDate(new Date());
        // RemoveFieldsleft and right double quotes

        modelProviderDTO.setFields(modelProviderDTO.getFields());
        ModelProviderEntity entity = ConvertUtils.sourceToTarget(modelProviderDTO, ModelProviderEntity.class);
        if (modelProviderDao.insert(entity) == 0) {
            throw new RenException(ErrorCode.ADD_DATA_FAILED);
        }

        return ConvertUtils.sourceToTarget(modelProviderDTO, ModelProviderDTO.class);
    }

    @Override
    public ModelProviderDTO edit(ModelProviderDTO modelProviderDTO) {
        UserDetail user = SecurityUser.getUser();
        modelProviderDTO.setUpdater(user.getId());
        modelProviderDTO.setUpdateDate(new Date());
        if (modelProviderDao
                .updateById(ConvertUtils.sourceToTarget(modelProviderDTO, ModelProviderEntity.class)) == 0) {
            throw new RenException(ErrorCode.UPDATE_DATA_FAILED);
        }
        return ConvertUtils.sourceToTarget(modelProviderDTO, ModelProviderDTO.class);
    }

    @Override
    public void delete(String id) {
        if (modelProviderDao.deleteById(id) == 0) {
            throw new RenException(ErrorCode.DELETE_DATA_FAILED);
        }
    }

    @Override
    public void delete(List<String> ids) {
        if (modelProviderDao.deleteBatchIds(ids) == 0) {
            throw new RenException(ErrorCode.DELETE_DATA_FAILED);
        }
    }

    @Override
    public List<ModelProviderDTO> getList(String modelType, String providerCode) {
        QueryWrapper<ModelProviderEntity> queryWrapper = new QueryWrapper<>();
        queryWrapper.eq("model_type", StringUtils.isBlank(modelType) ? "" : modelType);
        queryWrapper.eq("provider_code", StringUtils.isBlank(providerCode) ? "" : providerCode);
        List<ModelProviderEntity> providerEntities = modelProviderDao.selectList(queryWrapper);
        return ConvertUtils.sourceToTarget(providerEntities, ModelProviderDTO.class);
    }
}
