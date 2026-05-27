package tbot.modules.device.service;

import java.util.Date;
import java.util.List;
import java.util.Map;

import tbot.common.page.PageData;
import tbot.common.service.BaseService;
import tbot.modules.device.dto.DeviceManualAddDTO;
import tbot.modules.device.dto.DevicePageUserDTO;
import tbot.modules.device.dto.DeviceReportReqDTO;
import tbot.modules.device.dto.DeviceReportRespDTO;
import tbot.modules.device.entity.DeviceEntity;
import tbot.modules.device.vo.UserShowDeviceListVO;

public interface DeviceService extends BaseService<DeviceEntity> {
    /**
     * Get device online data
     */
    String getDeviceOnlineData(String agentId);

    /**
     * Check whether device activated
     */
    DeviceReportRespDTO checkDeviceActive(String macAddress, String clientId,
            DeviceReportReqDTO deviceReport);

    /**
     * Get device list of user specified agent,
     */
    List<DeviceEntity> getUserDevices(Long userId, String agentId);

    /**
     * Unbind Device
     */
    void unbindDevice(Long userId, String deviceId);

    /**
     * Device Activation
     */
    Boolean deviceActivation(String agentId, String activationCode);

    /**
     * Delete all devices of this user
     * 
     * @param userId Userid
     */
    void deleteByUserId(Long userId);

    /**
     * Delete all devices associated with specified agent
     * 
     * @param agentId Agentid
     */
    void deleteByAgentId(String agentId);

    /**
     * Get device count of specified user
     * 
     * @param userId Userid
     * @return Device count
     */
    Long selectCountByUserId(Long userId);

    /**
     * Paginated get all device info
     *
     * @param dto Paged search parameters
     * @return User list paged data
     */
    PageData<UserShowDeviceListVO> page(DevicePageUserDTO dto);

    /**
     * Based onMACGet device info by address
     * 
     * @param macAddress MACAddress
     * @return Device info
     */
    DeviceEntity getDeviceByMacAddress(String macAddress);

    /**
     * According to DeviceIDGet activation code
     * 
     * @param deviceId DeviceID
     * @return Activation code
     */
    String geCodeByDeviceId(String deviceId);

    /**
     * Get latest last connection time of this agent device
     * 
     * @param agentId Agentid
     * @return Return device most recent last connection time
     */
    Date getLatestLastConnectionTime(String agentId);

    /**
     * Manually add device
     */
    void manualAddDevice(Long userId, DeviceManualAddDTO dto);

    /**
     * Update device connection info
     */
    void updateDeviceConnectionInfo(String agentId, String deviceId, String appVersion);

    /**
     * GenerateWebSocketAuthtoken
     *
     * @param clientId ClientID
     * @param username Username(Usually isdeviceId)
     * @return AuthtokenString
     * @throws Exception Generatetokenexception when
     */
    String generateWebSocketToken(String clientId, String username) throws Exception;

    /**
     * Based onMACAddress search device
     *
     * @param macAddress MACAddress keyword
     * @param userId     UserID
     * @return Device List
     */
    List<DeviceEntity> searchDevicesByMacAddress(String macAddress, Long userId);

    /**
     * Get device tool list
     */
    Object getDeviceTools(String deviceId);

    /**
     * Call device tool
     */
    Object callDeviceTool(String deviceId, String toolName, Map<String, Object> arguments);

}