package tbot.modules.sys.service.impl;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;

import lombok.AllArgsConstructor;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.page.PageData;
import tbot.common.service.impl.BaseServiceImpl;
import tbot.common.utils.ConvertUtils;
import tbot.common.utils.JsonUtils;
import tbot.common.utils.SM2Utils;
import tbot.modules.sys.dao.SysParamsDao;
import tbot.modules.sys.dto.SysParamsDTO;
import tbot.modules.sys.entity.SysParamsEntity;
import tbot.modules.sys.redis.SysParamsRedis;
import tbot.modules.sys.service.SysParamsService;

/**
 * Parameter management
 */
@AllArgsConstructor
@Service
public class SysParamsServiceImpl extends BaseServiceImpl<SysParamsDao, SysParamsEntity> implements SysParamsService {
    private final SysParamsRedis sysParamsRedis;

    @Override
    public PageData<SysParamsDTO> page(Map<String, Object> params) {
        IPage<SysParamsEntity> page = baseDao.selectPage(
                getPage(params, null, false),
                getWrapper(params));

        return getPageData(page, SysParamsDTO.class);
    }

    @Override
    public List<SysParamsDTO> list(Map<String, Object> params) {
        List<SysParamsEntity> entityList = baseDao.selectList(getWrapper(params));

        return ConvertUtils.sourceToTarget(entityList, SysParamsDTO.class);
    }

    private QueryWrapper<SysParamsEntity> getWrapper(Map<String, Object> params) {
        String paramCode = (String) params.get("paramCode");

        QueryWrapper<SysParamsEntity> wrapper = new QueryWrapper<>();
        wrapper.eq("param_type", 1);
        wrapper.nested(StringUtils.isNotBlank(paramCode), i -> i.like("param_code", paramCode)
                .or()
                .like("remark", paramCode));

        return wrapper;
    }

    @Override
    public SysParamsDTO get(Long id) {
        SysParamsEntity entity = baseDao.selectById(id);

        return ConvertUtils.sourceToTarget(entity, SysParamsDTO.class);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void save(SysParamsDTO dto) {
        validateParamValue(dto);

        SysParamsEntity entity = ConvertUtils.sourceToTarget(dto, SysParamsEntity.class);
        insert(entity);

        sysParamsRedis.set(entity.getParamCode(), entity.getParamValue());
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void update(SysParamsDTO dto) {
        validateParamValue(dto);
        detectingSMSParameters(dto.getParamCode(), dto.getParamValue());
        SysParamsEntity entity = ConvertUtils.sourceToTarget(dto, SysParamsEntity.class);
        updateById(entity);

        sysParamsRedis.set(entity.getParamCode(), entity.getParamValue());
    }

    /**
     * ValidateParameter valueType
     */
    private void validateParamValue(SysParamsDTO dto) {
        if (dto == null) {
            throw new RenException(ErrorCode.PARAM_VALUE_NULL);
        }

        if (StringUtils.isBlank(dto.getParamValue())) {
            throw new RenException(ErrorCode.PARAM_VALUE_NULL);
        }

        if (StringUtils.isBlank(dto.getValueType())) {
            throw new RenException(ErrorCode.PARAM_TYPE_NULL);
        }

        String valueType = dto.getValueType().toLowerCase();
        String paramValue = dto.getParamValue();

        switch (valueType) {
            case "string":
                break;
            case "array":
                break;
            case "number":
                try {
                    Double.parseDouble(paramValue);
                } catch (NumberFormatException e) {
                    throw new RenException(ErrorCode.PARAM_NUMBER_INVALID);
                }
                break;
            case "boolean":
                if (!"true".equalsIgnoreCase(paramValue) && !"false".equalsIgnoreCase(paramValue)) {
                    throw new RenException(ErrorCode.PARAM_BOOLEAN_INVALID);
                }
                break;
            case "json":
                try {
                    // First check whether starts with { Start With } End
                    String trimmedValue = paramValue.trim();
                    if (!trimmedValue.startsWith("{") || !trimmedValue.endsWith("}")) {
                        throw new RenException(ErrorCode.PARAM_JSON_INVALID);
                    }
                    // Then try parseJSON
                    JsonUtils.parseObject(paramValue, Object.class);
                } catch (Exception e) {
                    throw new RenException(ErrorCode.PARAM_JSON_INVALID);
                }
                break;
            default:
                throw new RenException(ErrorCode.PARAM_TYPE_INVALID);
        }
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void delete(String[] ids) {
        // DeleteRedisData
        List<String> paramCodeList = baseDao.getParamCodeList(ids);
        String[] paramCodes = paramCodeList.toArray(new String[paramCodeList.size()]);
        if (paramCodes.length > 0) {
            sysParamsRedis.delete(paramCodes);
        }

        // Delete
        deleteBatchIds(Arrays.asList(ids));
    }

    @Override
    public String getValue(String paramCode, Boolean fromCache) {
        String paramValue = null;
        if (fromCache) {
            try {
                paramValue = sysParamsRedis.get(paramCode);
            } catch (RuntimeException e) {
                log.warn("Invalid sys_params cache entry ignored for code: " + paramCode);
                sysParamsRedis.delete(new Object[] { paramCode });
            }
            if (paramValue == null) {
                paramValue = baseDao.getValueByCode(paramCode);

                sysParamsRedis.set(paramCode, paramValue);
            }
        } else {
            paramValue = baseDao.getValueByCode(paramCode);
        }
        return paramValue;
    }

    @Override
    public <T> T getValueObject(String paramCode, Class<T> clazz) {
        String paramValue = getValue(paramCode, true);
        if (StringUtils.isNotBlank(paramValue)) {
            return JsonUtils.parseObject(paramValue, clazz);
        }

        try {
            return clazz.getDeclaredConstructor().newInstance();
        } catch (Exception e) {
            throw new RenException(ErrorCode.PARAMS_GET_ERROR);
        }
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public int updateValueByCode(String paramCode, String paramValue) {
        int count = baseDao.updateValueByCode(paramCode, paramValue);
        sysParamsRedis.set(paramCode, paramValue);
        return count;
    }

    @Override
    public void initServerSecret() {
        // Get serverKey
        String secretParam = getValue(Constant.SERVER_SECRET, false);
        if (StringUtils.isBlank(secretParam) || "null".equals(secretParam)) {
            String newSecret = UUID.randomUUID().toString();
            updateValueByCode(Constant.SERVER_SECRET, newSecret);
        }

        // InitializeSM2Keypair
        initSM2KeyPair();
    }

    /**
     * InitializeSM2Keypair
     */
    private void initSM2KeyPair() {
        // GetSM2Public key
        String publicKey = getValue(Constant.SM2_PUBLIC_KEY, false);
        // GetSM2Private key
        String privateKey = getValue(Constant.SM2_PRIVATE_KEY, false);

        // If public key or private key empty, generate newKeypair
        if (StringUtils.isBlank(publicKey) || StringUtils.isBlank(privateKey) || 
            "null".equals(publicKey) || "null".equals(privateKey)) {
            Map<String, String> keyPair = SM2Utils.createKey();
            String newPublicKey = keyPair.get(SM2Utils.KEY_PUBLIC_KEY);
            String newPrivateKey = keyPair.get(SM2Utils.KEY_PRIVATE_KEY);

            // Update in databaseKeypair
            updateValueByCode(Constant.SM2_PUBLIC_KEY, newPublicKey);
            updateValueByCode(Constant.SM2_PRIVATE_KEY, newPrivateKey);
        }
    }

    /**
     * Check SMS parameters meet requirements
     * 
     * @param paramCode  Parameter code
     * @param paramValue Parameter value
     * @return Pass
     */
    private boolean detectingSMSParameters(String paramCode, String paramValue) {
        // Determine whether enable phoneRegisterofParameter codeIf notParameter code, no need check other SMS parameters, return directlytrue
        if (!Constant.SysMSMParam.SERVER_ENABLE_MOBILE_REGISTER.getValue().equals(paramCode)) {
            return true;
        }
        // Determine whether closed, if closed SMSRegister, no need check other SMS parameters, return directlytrue
        if ("false".equalsIgnoreCase(paramValue)) {
            return true;
        }
        // Check SMS related parameters are empty
        ArrayList<String> list = new ArrayList<String>();
        list.add(Constant.SysMSMParam.SERVER_SMS_MAX_SEND_COUNT.getValue());
        list.add(Constant.SysMSMParam.ALIYUN_SMS_ACCESS_KEY_ID.getValue());
        list.add(Constant.SysMSMParam.ALIYUN_SMS_ACCESS_KEY_SECRET.getValue());
        list.add(Constant.SysMSMParam.ALIYUN_SMS_SIGN_NAME.getValue());
        list.add(Constant.SysMSMParam.ALIYUN_SMS_SMS_CODE_TEMPLATE_CODE.getValue());
        StringBuilder str = new StringBuilder();
        list.forEach(item -> {
            if (!StringUtils.isNoneBlank(item)) {
                str.append(",").append(item);
            }
        });
        if (!str.isEmpty()) {
            String promptStr = "%s these parameters cannot be empty";
            String substring = str.substring(1, str.length());
            throw new RenException(promptStr.formatted(substring));
        }
        return true;
    }
}
