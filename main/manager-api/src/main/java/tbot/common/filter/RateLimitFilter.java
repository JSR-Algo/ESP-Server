package tbot.common.filter;

import java.io.IOException;
import java.util.Arrays;
import java.util.List;

import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import tbot.common.exception.ErrorCode;
import tbot.common.redis.RedisUtils;
import tbot.common.utils.Result;

/**
 * Rate limiting filter for auth endpoints.
 * Sliding window: 5 requests per minute per IP.
 */
@Component
public class RateLimitFilter extends OncePerRequestFilter {

    private static final List<String> RATE_LIMITED_PATHS = Arrays.asList(
            "/user/login",
            "/user/register",
            "/user/captcha",
            "/user/smsVerification"
    );
    private static final int MAX_REQUESTS = 5;
    private static final long WINDOW_SECONDS = 60;

    private final RedisUtils redisUtils;

    public RateLimitFilter(RedisUtils redisUtils) {
        this.redisUtils = redisUtils;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        String path = request.getServletPath();
        if (!shouldRateLimit(path)) {
            filterChain.doFilter(request, response);
            return;
        }

        String clientIp = getClientIp(request);
        String key = "rate_limit:ip:" + clientIp + ":" + path;

        try {
            Long count = redisUtils.increment(key, WINDOW_SECONDS);
            if (count != null && count > MAX_REQUESTS) {
                response.setStatus(429);
                response.setContentType("application/json;charset=UTF-8");
                Result<Void> result = new Result<>();
                result.setCode(ErrorCode.RATE_LIMIT_EXCEEDED);
                result.setMsg("Too many requests. Please try again later.");
                response.getWriter().write(toJson(result));
                return;
            }
        } catch (Exception e) {
            // Fail open: if Redis is unavailable, do not block requests
            // Log the error for monitoring
        }

        filterChain.doFilter(request, response);
    }

    private boolean shouldRateLimit(String path) {
        return RATE_LIMITED_PATHS.stream().anyMatch(path::endsWith);
    }

    private String getClientIp(HttpServletRequest request) {
        String ip = request.getHeader("X-Forwarded-For");
        if (ip == null || ip.isEmpty() || "unknown".equalsIgnoreCase(ip)) {
            ip = request.getHeader("Proxy-Client-IP");
        }
        if (ip == null || ip.isEmpty() || "unknown".equalsIgnoreCase(ip)) {
            ip = request.getHeader("WL-Proxy-Client-IP");
        }
        if (ip == null || ip.isEmpty() || "unknown".equalsIgnoreCase(ip)) {
            ip = request.getRemoteAddr();
        }
        if (ip != null && ip.contains(",")) {
            ip = ip.split(",")[0].trim();
        }
        return ip != null ? ip : "unknown";
    }

    private String toJson(Result<Void> result) {
        return "{\"code\":" + result.getCode() + ",\"msg\":\"" + result.getMsg() + "\"}";
    }
}
