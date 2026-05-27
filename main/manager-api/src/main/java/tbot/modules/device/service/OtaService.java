package tbot.modules.device.service;

import java.util.Map;

import tbot.common.page.PageData;
import tbot.common.service.BaseService;
import tbot.modules.device.entity.OtaEntity;

/**
 * OTAFirmware management
 */
public interface OtaService extends BaseService<OtaEntity> {
    PageData<OtaEntity> page(Map<String, Object> params);

    boolean save(OtaEntity entity);

    void update(OtaEntity entity);

    void delete(String[] ids);

    OtaEntity getLatestOta(String type);
}