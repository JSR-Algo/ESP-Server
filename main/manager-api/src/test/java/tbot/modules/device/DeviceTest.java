package tbot.modules.device;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import tbot.common.redis.RedisUtils;
import tbot.modules.sys.dto.SysUserDTO;
import tbot.modules.sys.service.SysUserService;

@DisplayName("设备测试")
class DeviceTest {

    private RedisUtils redisUtils;
    private SysUserService sysUserService;

    @BeforeEach
    void setUp() {
        redisUtils = mock(RedisUtils.class);
        sysUserService = mock(SysUserService.class);
    }

    @Test
    @DisplayName("保存用户时将 DTO 传给用户服务")
    void testSaveUser() {
        SysUserDTO userDTO = new SysUserDTO();
        userDTO.setUsername("test");
        userDTO.setPassword(UUID.randomUUID().toString());

        assertDoesNotThrow(() -> sysUserService.save(userDTO));

        verify(sysUserService).save(userDTO);
    }

    @Test
    @DisplayName("写入设备激活信息时使用预期 Redis key")
    void testWriteDeviceInfo() {
        String macAddress = "00:11:22:33:44:66";
        String deviceCode = "123456";

        Map<String, Object> activationData = new HashMap<>();
        activationData.put("mac_address", macAddress);
        activationData.put("activation_code", deviceCode);
        activationData.put("board", "硬件型号");
        activationData.put("app_version", "0.3.13");

        String safeDeviceId = macAddress.replace(":", "_").toLowerCase();
        String cacheDeviceKey = String.format("ota:activation:data:%s", safeDeviceId);
        String redisKey = "ota:activation:code:" + deviceCode;

        redisUtils.set(cacheDeviceKey, activationData, 300);
        redisUtils.set(redisKey, macAddress, 300);

        verify(redisUtils).set(cacheDeviceKey, activationData, 300);
        verify(redisUtils).set(redisKey, macAddress, 300);

        assertEquals(macAddress, activationData.get("mac_address"));
        assertEquals(deviceCode, activationData.get("activation_code"));
        assertEquals("硬件型号", activationData.get("board"));
        assertEquals("0.3.13", activationData.get("app_version"));
        assertEquals(safeDeviceId, "00_11_22_33_44_66");
    }
}
