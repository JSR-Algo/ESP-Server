package tbot.modules.security.config;

import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.filter.DelegatingFilterProxy;

import tbot.common.filter.OpenRedirectPreventionFilter;
import tbot.common.filter.RateLimitFilter;
import tbot.common.filter.SecurityHeadersFilter;

/**
 * FilterConfig
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
@Configuration
public class FilterConfig {

    @Bean
    public FilterRegistrationBean<DelegatingFilterProxy> shiroFilterRegistration() {
        FilterRegistrationBean<DelegatingFilterProxy> registration = new FilterRegistrationBean<>();
        registration.setFilter(new DelegatingFilterProxy("shiroFilter"));
        // Default value is false, indicates lifecycle by Spring ApplicationContext management, set to true then means by ServletContainer Manage
        registration.addInitParameter("targetFilterLifecycle", "true");
        registration.setEnabled(true);
        registration.setOrder(Integer.MAX_VALUE - 1);
        registration.addUrlPatterns("/*");
        return registration;
    }

    @Bean
    public FilterRegistrationBean<RateLimitFilter> rateLimitFilterRegistration(RateLimitFilter rateLimitFilter) {
        FilterRegistrationBean<RateLimitFilter> registration = new FilterRegistrationBean<>();
        registration.setFilter(rateLimitFilter);
        registration.setOrder(Integer.MAX_VALUE - 3);
        registration.addUrlPatterns("/user/login", "/user/register", "/user/captcha", "/user/smsVerification");
        return registration;
    }

    @Bean
    public FilterRegistrationBean<SecurityHeadersFilter> securityHeadersFilterRegistration(SecurityHeadersFilter securityHeadersFilter) {
        FilterRegistrationBean<SecurityHeadersFilter> registration = new FilterRegistrationBean<>();
        registration.setFilter(securityHeadersFilter);
        registration.setOrder(Integer.MAX_VALUE - 4);
        registration.addUrlPatterns("/*");
        return registration;
    }

    @Bean
    public FilterRegistrationBean<OpenRedirectPreventionFilter> openRedirectFilterRegistration() {
        FilterRegistrationBean<OpenRedirectPreventionFilter> registration = new FilterRegistrationBean<>();
        registration.setFilter(new OpenRedirectPreventionFilter());
        registration.setOrder(Integer.MAX_VALUE - 5);
        registration.addUrlPatterns("/*");
        return registration;
    }
}
