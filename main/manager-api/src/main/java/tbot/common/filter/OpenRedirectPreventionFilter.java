package tbot.common.filter;

import java.io.IOException;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import jakarta.servlet.Filter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.FilterConfig;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletRequest;
import jakarta.servlet.ServletResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.servlet.http.HttpServletResponseWrapper;

/**
 * Open Redirect Prevention Filter
 * Wraps the HttpServletResponse to validate any Location header values
 * against an allowlist of local paths. Blocks redirects to external domains.
 */
public class OpenRedirectPreventionFilter implements Filter {

    private static final Logger logger = LoggerFactory.getLogger(OpenRedirectPreventionFilter.class);

    // Allowlist of permitted redirect targets (local paths only)
    private static final Set<String> ALLOWED_REDIRECT_PATHS = new HashSet<>(Arrays.asList(
            "/tbot/user/login",
            "/tbot/",
            "/tbot/index.html"
    ));

    @Override
    public void init(FilterConfig filterConfig) throws ServletException {
        // No initialization required
    }

    @Override
    public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
            throws IOException, ServletException {
        HttpServletRequest httpRequest = (HttpServletRequest) request;
        HttpServletResponse httpResponse = (HttpServletResponse) response;

        SafeRedirectResponseWrapper wrappedResponse = new SafeRedirectResponseWrapper(httpResponse, httpRequest);
        chain.doFilter(request, wrappedResponse);
    }

    @Override
    public void destroy() {
        // No cleanup required
    }

    /**
     * Validates whether a redirect URL is safe (local path on allowlist or same-origin relative).
     */
    static boolean isRedirectAllowed(String location, HttpServletRequest request) {
        if (location == null || location.isEmpty()) {
            return true;
        }

        // Reject absolute URLs pointing to external domains (http://, https://, //)
        if (location.startsWith("http://") || location.startsWith("https://") || location.startsWith("//")) {
            logger.warn("Blocked external redirect attempt to: {}", location);
            return false;
        }

        // Normalize path: strip query string and fragment
        String path = location;
        int queryIdx = path.indexOf('?');
        if (queryIdx >= 0) {
            path = path.substring(0, queryIdx);
        }
        int fragmentIdx = path.indexOf('#');
        if (fragmentIdx >= 0) {
            path = path.substring(0, fragmentIdx);
        }

        // Allow same-origin relative paths that start with the context path
        String contextPath = request.getContextPath();
        if (contextPath != null && !contextPath.isEmpty() && path.startsWith(contextPath)) {
            String relativePath = path.substring(contextPath.length());
            if (relativePath.isEmpty()) {
                relativePath = "/";
            }
            // Only allow if the relative path is on the allowlist
            if (ALLOWED_REDIRECT_PATHS.contains(path)) {
                return true;
            }
        }

        // Also allow exact allowlist paths
        if (ALLOWED_REDIRECT_PATHS.contains(path)) {
            return true;
        }

        logger.warn("Blocked redirect to non-allowlisted path: {}", location);
        return false;
    }

    /**
     * Response wrapper that intercepts sendRedirect and validates the target.
     */
    static class SafeRedirectResponseWrapper extends HttpServletResponseWrapper {
        private final HttpServletRequest request;

        public SafeRedirectResponseWrapper(HttpServletResponse response, HttpServletRequest request) {
            super(response);
            this.request = request;
        }

        @Override
        public void sendRedirect(String location) throws IOException {
            if (!isRedirectAllowed(location, request)) {
                // Instead of redirecting, return 403 Forbidden
                setStatus(HttpServletResponse.SC_FORBIDDEN);
                getWriter().write("{\"code\":403,\"msg\":\"Redirect blocked for security reasons\"}");
                getWriter().flush();
                return;
            }
            super.sendRedirect(location);
        }

        @Override
        public void setHeader(String name, String value) {
            if ("Location".equalsIgnoreCase(name) && !isRedirectAllowed(value, request)) {
                logger.warn("Blocked Location header set to unsafe URL: {}", value);
                // Drop the unsafe Location header
                return;
            }
            super.setHeader(name, value);
        }

        @Override
        public void addHeader(String name, String value) {
            if ("Location".equalsIgnoreCase(name) && !isRedirectAllowed(value, request)) {
                logger.warn("Blocked Location header add to unsafe URL: {}", value);
                return;
            }
            super.addHeader(name, value);
        }
    }
}
