package tbot.common.filter;

import java.io.IOException;
import java.util.Arrays;
import java.util.List;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
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

    private static final Logger log = LoggerFactory.getLogger(RateLimitFilter.class);

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
            // Fail open: if Redis is unavailable, do not block requests.
            // Log so the fail-open branch is observable in monitoring/alerting
            // (silent fail-open would let an outage disable rate limiting unnoticed).
            log.warn("Rate limit check failed for key={}, failing open (request allowed). Reason: {}",
                    key, e.toString(), e);
        }

        filterChain.doFilter(request, response);
    }

    private boolean shouldRateLimit(String path) {
        return RATE_LIMITED_PATHS.stream().anyMatch(path::endsWith);
    }

    /**
     * Resolves the client IP used as the rate-limit key.
     *
     * <p>Deliberately uses {@link HttpServletRequest#getRemoteAddr()} only and does NOT trust
     * forwarded headers (X-Forwarded-For, Proxy-Client-IP, WL-Proxy-Client-IP). Those headers are
     * fully attacker-controlled: a client can send a unique X-Forwarded-For per request and rotate
     * the limiter key on every call, bypassing the limit entirely. getRemoteAddr() returns the
     * actual TCP peer address, which the caller cannot spoof.
     *
     * <p>If this service is ever placed behind a trusted reverse proxy/load balancer, the correct
     * fix is to parse the rightmost untrusted hop from X-Forwarded-For against a configured list of
     * trusted upstream addresses — NOT to blindly trust the leftmost value. Until such trust can be
     * established, getRemoteAddr() is the safe key source.
     */
    private String getClientIp(HttpServletRequest request) {
        String ip = request.getRemoteAddr();
        return (ip != null && !ip.isEmpty()) ? ip : "unknown";
    }

    private String toJson(Result<Void> result) {
        return "{\"code\":" + result.getCode() + ",\"msg\":\"" + result.getMsg() + "\"}";
    }
}
