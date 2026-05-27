package tbot.modules.sys;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import org.mockito.ArgumentCaptor;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import tbot.common.constant.Constant;
import tbot.modules.sys.dao.SysParamsDao;
import tbot.modules.sys.redis.SysParamsRedis;
import tbot.modules.sys.service.impl.SysParamsServiceImpl;

class SysParamsServiceImplTest {

    @Test
    @DisplayName("getValue falls back to DB when cached raw URL cannot be deserialized")
    void getValueFallsBackToDbWhenCacheEntryIsInvalid() {
        SysParamsRedis redis = mock(SysParamsRedis.class);
        SysParamsDao dao = mock(SysParamsDao.class);
        SysParamsServiceImpl service = new SysParamsServiceImpl(redis);
        ReflectionTestUtils.setField(service, "baseDao", dao);

        when(redis.get(Constant.SERVER_WEBSOCKET)).thenThrow(new RuntimeException("bad redis payload"));
        when(dao.getValueByCode(Constant.SERVER_WEBSOCKET)).thenReturn("ws://192.168.0.114:8000/tbot/v1/");

        String value = service.getValue(Constant.SERVER_WEBSOCKET, true);

        assertEquals("ws://192.168.0.114:8000/tbot/v1/", value);
        ArgumentCaptor<Object[]> deletedKeys = ArgumentCaptor.forClass(Object[].class);
        verify(redis).delete(deletedKeys.capture());
        assertEquals(Constant.SERVER_WEBSOCKET, deletedKeys.getValue()[0]);
        verify(redis).set(Constant.SERVER_WEBSOCKET, "ws://192.168.0.114:8000/tbot/v1/");
    }
}
