package tbot.modules.security.user;

import org.apache.shiro.SecurityUtils;
import org.apache.shiro.subject.Subject;

import tbot.common.user.UserDetail;

/**
 * ShiroUtility class
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
public class SecurityUser {

    public static Subject getSubject() {
        try {
            return SecurityUtils.getSubject();
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * Get user info
     */
    public static UserDetail getUser() {
        Subject subject = getSubject();
        if (subject == null) {
            return new UserDetail();
        }

        UserDetail user = (UserDetail) subject.getPrincipal();
        if (user == null) {
            return new UserDetail();
        }

        return user;
    }

    public static String getToken() {
        return getUser().getToken();
    }

    /**
     * Get UserID
     */
    public static Long getUserId() {
        return getUser().getId();
    }
}