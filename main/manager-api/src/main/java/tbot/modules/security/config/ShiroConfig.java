package tbot.modules.security.config;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

import org.apache.shiro.mgt.SecurityManager;
import org.apache.shiro.session.mgt.SessionManager;
import org.apache.shiro.spring.LifecycleBeanPostProcessor;
import org.apache.shiro.spring.security.interceptor.AuthorizationAttributeSourceAdvisor;
import org.apache.shiro.spring.web.ShiroFilterFactoryBean;
import org.apache.shiro.web.config.ShiroFilterConfiguration;
import org.apache.shiro.web.mgt.DefaultWebSecurityManager;
import org.apache.shiro.web.session.mgt.DefaultWebSessionManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import jakarta.servlet.Filter;
import tbot.modules.security.oauth2.Oauth2Filter;
import tbot.modules.security.oauth2.Oauth2Realm;
import tbot.modules.security.secret.ServerSecretFilter;
import tbot.modules.sys.service.SysParamsService;

/**
 * ShiroConfig file of
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
@Configuration
public class ShiroConfig {

    @Bean
    public DefaultWebSessionManager sessionManager() {
        DefaultWebSessionManager sessionManager = new DefaultWebSessionManager();
        sessionManager.setSessionValidationSchedulerEnabled(false);
        sessionManager.setSessionIdUrlRewritingEnabled(false);

        return sessionManager;
    }

    @Bean("securityManager")
    public SecurityManager securityManager(Oauth2Realm oAuth2Realm, SessionManager sessionManager) {
        DefaultWebSecurityManager securityManager = new DefaultWebSecurityManager();
        securityManager.setRealm(oAuth2Realm);
        securityManager.setSessionManager(sessionManager);
        securityManager.setRememberMeManager(null);
        return securityManager;
    }

    @Bean("shiroFilter")
    public ShiroFilterFactoryBean shirFilter(SecurityManager securityManager, SysParamsService sysParamsService) {
        ShiroFilterConfiguration config = new ShiroFilterConfiguration();
        config.setFilterOncePerRequest(true);

        ShiroFilterFactoryBean shiroFilter = new ShiroFilterFactoryBean();
        shiroFilter.setSecurityManager(securityManager);
        shiroFilter.setShiroFilterConfiguration(config);

        // SECURITY FIX: Explicitly set fixed URLs to prevent any open-redirect behavior
        // in Shiro's default authentication filters (CVE-2023-46749 style path normalization
        // or redirect parameter injection). We never redirect to external URLs.
        shiroFilter.setLoginUrl("/tbot/user/login");
        shiroFilter.setUnauthorizedUrl("/tbot/user/login");
        shiroFilter.setSuccessUrl("/tbot/");

        Map<String, Filter> filters = new HashMap<>();
        // oauthFilter
        filters.put("oauth2", new Oauth2Filter());
        // Service key filter
        filters.put("server", new ServerSecretFilter(sysParamsService));
        shiroFilter.setFilters(filters);

        // AddShirobuilt-in filter of
        /*
         * anon: accessible without authentication
         * authc: must be authenticated before question
         * userMust have remember me function to access
         * permsNeed permission to certain resource to access
         * role: Must have role permission to access
         */
        Map<String, String> filterMap = new LinkedHashMap<>();
        filterMap.put("/ota/**", "anon");
        filterMap.put("/otaMag/download/**", "oauth2");
        filterMap.put("/webjars/**", "anon");
        // CRITICAL FIX: Druid monitoring console must NOT be anonymous.
        // Previously exposed without auth (P0). Now requires oauth2 token.
        filterMap.put("/druid/**", "oauth2");
        filterMap.put("/v3/api-docs/**", "oauth2");
        filterMap.put("/doc.html", "oauth2");
        filterMap.put("/favicon.ico", "anon");
        filterMap.put("/user/captcha", "anon");
        filterMap.put("/user/smsVerification", "anon");
        filterMap.put("/user/login", "anon");
        filterMap.put("/user/pub-config", "anon");
        filterMap.put("/user/register", "anon");
        filterMap.put("/user/retrieve-password", "anon");
        // willconfigPath UseserverService filter
        filterMap.put("/config/**", "server");
        filterMap.put("/agent/chat-history/report", "server");
        filterMap.put("/agent/chat-history/download/**", "anon");
        filterMap.put("/agent/chat-summary/**", "server");
        filterMap.put("/agent/chat-title/**", "server");
        filterMap.put("/agent/play/**", "anon");
        filterMap.put("/voiceClone/play/**", "anon");
        filterMap.put("/**", "oauth2");
        shiroFilter.setFilterChainDefinitionMap(filterMap);

        return shiroFilter;
    }

    @Bean("lifecycleBeanPostProcessor")
    public LifecycleBeanPostProcessor lifecycleBeanPostProcessor() {
        return new LifecycleBeanPostProcessor();
    }

    @Bean
    public AuthorizationAttributeSourceAdvisor authorizationAttributeSourceAdvisor(SecurityManager securityManager) {
        AuthorizationAttributeSourceAdvisor advisor = new AuthorizationAttributeSourceAdvisor();
        advisor.setSecurityManager(securityManager);
        return advisor;
    }
}
