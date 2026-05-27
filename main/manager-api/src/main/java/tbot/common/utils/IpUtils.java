package tbot.common.utils;

import org.apache.commons.lang3.StringUtils;

import jakarta.servlet.http.HttpServletRequest;
import lombok.extern.slf4j.Slf4j;

/**
 * IPAddress
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
@Slf4j
public class IpUtils {
    /**
     * GetIPAddress
     * <p>
     * UseNginxAnd other reverse proxy software, cannot passrequest.getRemoteAddr()GetIPAddress
     * If multi-level reverse proxy used,X-Forwarded-Forvalue not only one, but stringIPAddress,X-Forwarded-ForFirst non inunknownValid ofIPString, then realIPAddress
     */
    public static String getIpAddr(HttpServletRequest request) {
        String unknown = "unknown";
        String ip = null;
        try {
            ip = request.getHeader("x-forwarded-for");
            if (StringUtils.isEmpty(ip) || unknown.equalsIgnoreCase(ip)) {
                ip = request.getHeader("Proxy-Client-IP");
            }
            if (StringUtils.isEmpty(ip) || ip.length() == 0 || unknown.equalsIgnoreCase(ip)) {
                ip = request.getHeader("WL-Proxy-Client-IP");
            }
            if (StringUtils.isEmpty(ip) || unknown.equalsIgnoreCase(ip)) {
                ip = request.getHeader("HTTP_CLIENT_IP");
            }
            if (StringUtils.isEmpty(ip) || unknown.equalsIgnoreCase(ip)) {
                ip = request.getHeader("HTTP_X_FORWARDED_FOR");
            }
            if (StringUtils.isEmpty(ip) || unknown.equalsIgnoreCase(ip)) {
                ip = request.getRemoteAddr();
            }
        } catch (Exception e) {
            log.error("IPUtils ERROR ", e);
        }

        return ip;
    }

}
