package tbot.modules.device.security;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import tbot.modules.device.security.DeviceChildProfileJwtVerifier.JwtRejection;

@Component
public class DeviceChildProfileAuthAudit {
    private static final Logger logger = LoggerFactory.getLogger(DeviceChildProfileAuthAudit.class);

    public void rejected(JwtRejection reason, String pathDeviceId) {
        logger.warn("child_profile_jwt_rejected reason={} deviceId={}",
                reason.name(), pathDeviceId == null ? "unavailable" : pathDeviceId);
    }
}
