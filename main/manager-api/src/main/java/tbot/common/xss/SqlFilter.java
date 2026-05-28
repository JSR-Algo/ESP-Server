package tbot.common.xss;

import org.apache.commons.lang3.StringUtils;

import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;

/**
 * SQLFilter
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 * @deprecated DEPRECATED: Rely on MyBatis #{} parameterization. Do not use for security.
 *             Blacklist filters are trivially bypassed and provide false confidence.
 */
@Deprecated
public class SqlFilter {

    /**
     * SQLInjection Filter
     *
     * @param str String to validate
     */
    public static String sqlInject(String str) {
        if (StringUtils.isBlank(str)) {
            return null;
        }
        // Remove'|"|;|\Character
        str = StringUtils.replace(str, "'", "");
        str = StringUtils.replace(str, "\"", "");
        str = StringUtils.replace(str, ";", "");
        str = StringUtils.replace(str, "\\", "");

        // Convert to lowercase
        str = str.toLowerCase();

        // Illegal Character
        String[] keywords = { "master", "truncate", "insert", "select", "delete", "update", "declare", "alter",
                "drop" };

        // Judge whether contains illegal characters
        for (String keyword : keywords) {
            if (str.contains(keyword)) {
                throw new RenException(ErrorCode.INVALID_SYMBOL);
            }
        }

        return str;
    }
}
