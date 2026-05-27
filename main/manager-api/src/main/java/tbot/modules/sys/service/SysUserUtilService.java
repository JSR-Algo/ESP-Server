package tbot.modules.sys.service;


import java.util.function.Consumer;

/**
 * Define system user utility class to avoid circular dependency with user module
 * If users and devices depend on each other, user needs get all devices, and device needs get username for each device
 * @author zjy
 * @since 2025-4-2
 */
public interface SysUserUtilService {
    /**
     * Assign username
     * @param userId Userid
     * @param setter Assignment Method
     */
    void assignUsername( Long userId, Consumer<String> setter);
}
