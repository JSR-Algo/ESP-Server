package tbot.modules.security.config;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;

import java.util.Map;

import org.apache.shiro.mgt.SecurityManager;
import org.apache.shiro.spring.web.ShiroFilterFactoryBean;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import tbot.modules.sys.service.SysParamsService;

/**
 * Security tests for Shiro configuration (unit tests, no Spring context).
 * Verifies that sensitive endpoints are not exposed anonymously.
 */
class ShiroConfigSecurityTest {

    @Test
    @DisplayName("Druid console must require authentication (not anon)")
    void druidRequiresAuth() throws Exception {
        ShiroConfig config = new ShiroConfig();
        SecurityManager securityManager = mock(SecurityManager.class);
        SysParamsService sysParamsService = mock(SysParamsService.class);

        ShiroFilterFactoryBean shiroFilter = config.shirFilter(securityManager, sysParamsService);
        assertNotNull(shiroFilter);

        Map<String, String> filterChain = shiroFilter.getFilterChainDefinitionMap();
        assertNotNull(filterChain);

        String druidChain = filterChain.get("/druid/**");
        assertNotNull(druidChain, "Druid path should have a filter chain defined");
        assertTrue(druidChain.contains("oauth2") || druidChain.contains("authc"),
                "Druid console must require authentication, found: " + druidChain);
        assertTrue(!druidChain.contains("anon"),
                "Druid console must NOT be anonymous, found: " + druidChain);
    }

    @Test
    @DisplayName("Shiro login URL is fixed to local path to prevent open redirect")
    void shiroLoginUrlIsFixed() throws Exception {
        ShiroConfig config = new ShiroConfig();
        SecurityManager securityManager = mock(SecurityManager.class);
        SysParamsService sysParamsService = mock(SysParamsService.class);

        ShiroFilterFactoryBean shiroFilter = config.shirFilter(securityManager, sysParamsService);
        assertNotNull(shiroFilter);

        assertEquals("/tbot/user/login", shiroFilter.getLoginUrl(),
                "Login URL must be a fixed local path");
        assertEquals("/tbot/user/login", shiroFilter.getUnauthorizedUrl(),
                "Unauthorized URL must be a fixed local path");
        assertEquals("/tbot/", shiroFilter.getSuccessUrl(),
                "Success URL must be a fixed local path");
    }

    @Test
    @DisplayName("Default catch-all requires oauth2 authentication")
    void defaultCatchAllRequiresAuth() throws Exception {
        ShiroConfig config = new ShiroConfig();
        SecurityManager securityManager = mock(SecurityManager.class);
        SysParamsService sysParamsService = mock(SysParamsService.class);

        ShiroFilterFactoryBean shiroFilter = config.shirFilter(securityManager, sysParamsService);
        assertNotNull(shiroFilter);

        Map<String, String> filterChain = shiroFilter.getFilterChainDefinitionMap();
        assertNotNull(filterChain);

        String catchAllChain = filterChain.get("/**");
        assertNotNull(catchAllChain, "Catch-all path should have a filter chain defined");
        assertTrue(catchAllChain.contains("oauth2"),
                "Catch-all must require oauth2 authentication, found: " + catchAllChain);
    }

    @Test
    @DisplayName("nginx proxy-auth endpoint performs its own token validation")
    void proxyAuthEndpointIsReachableForManualValidation() throws Exception {
        ShiroConfig config = new ShiroConfig();
        SecurityManager securityManager = mock(SecurityManager.class);
        SysParamsService sysParamsService = mock(SysParamsService.class);

        Map<String, String> filterChain = config
                .shirFilter(securityManager, sysParamsService)
                .getFilterChainDefinitionMap();

        assertEquals("anon", filterChain.get("/user/proxy-auth"));
    }
}
