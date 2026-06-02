package tbot.common.filter;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;

/**
 * Security tests for OpenRedirectPreventionFilter.
 */
class OpenRedirectPreventionFilterTest {

    private MockHttpServletRequest createRequest(String contextPath) {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setContextPath(contextPath);
        return request;
    }

    @Test
    @DisplayName("Absolute HTTP URL is blocked")
    void absoluteHttpUrlIsBlocked() {
        MockHttpServletRequest request = createRequest("/tbot");
        assertFalse(OpenRedirectPreventionFilter.isRedirectAllowed("http://evil.com", request));
    }

    @Test
    @DisplayName("Absolute HTTPS URL is blocked")
    void absoluteHttpsUrlIsBlocked() {
        MockHttpServletRequest request = createRequest("/tbot");
        assertFalse(OpenRedirectPreventionFilter.isRedirectAllowed("https://evil.com", request));
    }

    @Test
    @DisplayName("Protocol-relative URL is blocked")
    void protocolRelativeUrlIsBlocked() {
        MockHttpServletRequest request = createRequest("/tbot");
        assertFalse(OpenRedirectPreventionFilter.isRedirectAllowed("//evil.com", request));
    }

    @Test
    @DisplayName("Allowlisted local path is allowed")
    void allowlistedLocalPathIsAllowed() {
        MockHttpServletRequest request = createRequest("/tbot");
        assertTrue(OpenRedirectPreventionFilter.isRedirectAllowed("/tbot/user/login", request));
    }

    @Test
    @DisplayName("Non-allowlisted local path is blocked")
    void nonAllowlistedLocalPathIsBlocked() {
        MockHttpServletRequest request = createRequest("/tbot");
        assertFalse(OpenRedirectPreventionFilter.isRedirectAllowed("/tbot/some/other", request));
    }

    @Test
    @DisplayName("Null or empty location is allowed")
    void nullLocationIsAllowed() {
        MockHttpServletRequest request = createRequest("/tbot");
        assertTrue(OpenRedirectPreventionFilter.isRedirectAllowed(null, request));
        assertTrue(OpenRedirectPreventionFilter.isRedirectAllowed("", request));
    }

    @Test
    @DisplayName("Location with query string is validated on path only")
    void locationWithQueryStringIsValidated() {
        MockHttpServletRequest request = createRequest("/tbot");
        assertTrue(OpenRedirectPreventionFilter.isRedirectAllowed("/tbot/user/login?foo=bar", request));
        assertFalse(OpenRedirectPreventionFilter.isRedirectAllowed("/tbot/other?foo=bar", request));
    }
}
