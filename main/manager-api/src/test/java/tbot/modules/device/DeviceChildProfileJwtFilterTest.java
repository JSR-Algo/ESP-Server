package tbot.modules.device;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import tbot.modules.device.security.DeviceChildProfileAuthAudit;
import tbot.modules.device.security.DeviceChildProfileJwtFilter;
import tbot.modules.device.security.DeviceChildProfileJwtVerifier;
import tbot.modules.device.security.DeviceChildProfileJwtVerifier.JwtRejection;
import tbot.modules.device.security.DeviceChildProfileJwtVerifier.JwtRejectionException;

class DeviceChildProfileJwtFilterTest {
    @Test
    void auditsPathMismatchOnceWithoutAuthorizationOrProfileMaterial() throws Exception {
        DeviceChildProfileJwtVerifier verifier = mock(DeviceChildProfileJwtVerifier.class);
        DeviceChildProfileAuthAudit audit = mock(DeviceChildProfileAuthAudit.class);
        MockFilterChain chain = new MockFilterChain();
        MockHttpServletRequest request = request("/internal/devices/device-1/child-profile/secret-profile");
        request.addHeader("Authorization", "Bearer sensitive.jwt.value");

        new DeviceChildProfileJwtFilter(verifier, audit).doFilter(
                request, new MockHttpServletResponse(), chain);

        verify(audit).rejected(JwtRejection.PATH_MISMATCH, null);
        verifyNoMoreInteractions(audit);
        verify(verifier, never()).verify("Bearer sensitive.jwt.value", "device-1");
    }

    @Test
    void auditsEveryVerifierRejectionOnceUsingOnlyStableReasonAndSafePathDevice() throws Exception {
        for (JwtRejection reason : JwtRejection.values()) {
            if (reason == JwtRejection.PATH_MISMATCH) continue;
            DeviceChildProfileJwtVerifier verifier = mock(DeviceChildProfileJwtVerifier.class);
            DeviceChildProfileAuthAudit audit = mock(DeviceChildProfileAuthAudit.class);
            String authorization = "Bearer sensitive.jwt.profile-material";
            org.mockito.Mockito.doThrow(new JwtRejectionException(reason))
                    .when(verifier).verify(authorization, "device-1");
            MockHttpServletRequest request = request("/internal/devices/device-1/child-profile");
            request.addHeader("Authorization", authorization);

            new DeviceChildProfileJwtFilter(verifier, audit).doFilter(
                    request, new MockHttpServletResponse(), new MockFilterChain());

            verify(audit).rejected(reason, "device-1");
            verifyNoMoreInteractions(audit);
        }
    }

    @Test
    void doesNotAuditUnsafeUnboundedPathDeviceMaterial() throws Exception {
        DeviceChildProfileJwtVerifier verifier = mock(DeviceChildProfileJwtVerifier.class);
        DeviceChildProfileAuthAudit audit = mock(DeviceChildProfileAuthAudit.class);
        String unsafe = "x".repeat(200);
        when(verifier.toString()).thenReturn("unused");
        org.mockito.Mockito.doThrow(new JwtRejectionException(JwtRejection.MISSING_AUTHORIZATION))
                .when(verifier).verify(null, unsafe);

        new DeviceChildProfileJwtFilter(verifier, audit).doFilter(
                request("/internal/devices/" + unsafe + "/child-profile"),
                new MockHttpServletResponse(), new MockFilterChain());

        verify(audit).rejected(JwtRejection.MISSING_AUTHORIZATION, null);
    }

    private static MockHttpServletRequest request(String servletPath) {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setServletPath(servletPath);
        return request;
    }
}
