package tbot.common.xss;

import java.util.Collections;
import java.util.List;

import org.springframework.boot.context.properties.ConfigurationProperties;

import lombok.Data;

/**
 * XSS Config item
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
@Data
@ConfigurationProperties(prefix = "renren.xss")
public class XssProperties {
    /**
     * Enabled XSS
     */
    private boolean enabled;
    /**
     * ExcludedURLList
     */
    private List<String> excludeUrls = Collections.emptyList();
}
