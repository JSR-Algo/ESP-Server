package tbot.modules.security.service.impl;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.lang.reflect.Field;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletResponse;

import com.wf.captcha.SpecCaptcha;

class CaptchaServiceImplTest {
    private static final String E2E_CODE = "E2E42";

    private CaptchaServiceImpl service;

    @BeforeEach
    void setUp() throws Exception {
        service = new CaptchaServiceImpl();
        setField("open", false);
    }

    @Test
    void e2eCaptchaRendersValidatesAndIsConsumed() throws Exception {
        setField("e2eCaptchaEnabled", true);
        setField("e2eCaptchaCode", E2E_CODE);

        service.create(new MockHttpServletResponse(), "browser-login");

        assertTrue(service.validate("browser-login", E2E_CODE, true));
        assertFalse(service.validate("browser-login", E2E_CODE, true));
    }

    @Test
    void deterministicCodeIsIgnoredWhenE2eGateIsDisabled() throws Exception {
        setField("e2eCaptchaEnabled", false);
        setField("e2eCaptchaCode", E2E_CODE);
        SpecCaptcha generatedCaptcha = mock(SpecCaptcha.class);
        when(generatedCaptcha.text()).thenReturn("R4ND0");

        assertEquals("R4ND0", service.captchaTextForCache(generatedCaptcha));
    }

    @Test
    void invalidE2eCodeDoesNotEnableDeterministicCaptcha() throws Exception {
        setField("e2eCaptchaEnabled", true);
        setField("e2eCaptchaCode", "TOO-LONG");
        SpecCaptcha generatedCaptcha = mock(SpecCaptcha.class);
        when(generatedCaptcha.text()).thenReturn("R4ND0");

        assertEquals("R4ND0", service.captchaTextForCache(generatedCaptcha));
    }

    private void setField(String name, Object value) throws Exception {
        Field field = CaptchaServiceImpl.class.getDeclaredField(name);
        field.setAccessible(true);
        field.set(service, value);
    }
}
