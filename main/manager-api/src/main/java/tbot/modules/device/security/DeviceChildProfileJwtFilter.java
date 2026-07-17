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
    private static final Pattern SAFE_DEVICE_ID = Pattern.compile("^[A-Za-z0-9._:-]{1,128}$");
    private final DeviceChildProfileJwtVerifier verifier;
    private final DeviceChildProfileAuthAudit audit;

    public DeviceChildProfileJwtFilter(DeviceChildProfileJwtVerifier verifier) {
        this(verifier, new DeviceChildProfileAuthAudit());
    }

    public DeviceChildProfileJwtFilter(DeviceChildProfileJwtVerifier verifier, DeviceChildProfileAuthAudit audit) {
        this.verifier = verifier;
        this.audit = audit;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        Matcher matcher = PATH.matcher(request.getServletPath());
        if (!matcher.matches()) {
            audit.rejected(DeviceChildProfileJwtVerifier.JwtRejection.PATH_MISMATCH, null);
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED);
            return;
        }
        String pathDeviceId = matcher.group(1);
        String auditedDeviceId = SAFE_DEVICE_ID.matcher(pathDeviceId).matches() ? pathDeviceId : null;
        try {
            if (verifier == null) {
                throw new DeviceChildProfileJwtVerifier.JwtRejectionException(
                        DeviceChildProfileJwtVerifier.JwtRejection.VERIFIER_UNAVAILABLE);
            }
            verifier.verify(request.getHeader("Authorization"), pathDeviceId);
            filterChain.doFilter(request, response);
        } catch (DeviceChildProfileJwtVerifier.JwtRejectionException exception) {
            audit.rejected(exception.reason(), auditedDeviceId);
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED);
        }
    }
}
