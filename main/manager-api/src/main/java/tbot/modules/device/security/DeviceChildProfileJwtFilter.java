package tbot.modules.device.security;

import java.io.IOException;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.springframework.web.filter.OncePerRequestFilter;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

public class DeviceChildProfileJwtFilter extends OncePerRequestFilter {
    private static final Pattern PATH = Pattern.compile("^/internal/devices/([^/]+)/child-profile$");
    private final DeviceChildProfileJwtVerifier verifier;

    public DeviceChildProfileJwtFilter(DeviceChildProfileJwtVerifier verifier) {
        this.verifier = verifier;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        Matcher matcher = PATH.matcher(request.getServletPath());
        if (!matcher.matches()) {
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED);
            return;
        }
        try {
            if (verifier == null) {
                throw new SecurityException("child-profile JWT verifier is unavailable");
            }
            verifier.verify(request.getHeader("Authorization"), matcher.group(1));
            filterChain.doFilter(request, response);
        } catch (SecurityException exception) {
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED);
        }
    }
}
