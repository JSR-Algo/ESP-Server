package tbot.modules.security.controller;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultHandlers.print;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.Date;
import java.util.stream.Stream;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.NullSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.context.MessageSource;
import org.springframework.context.support.StaticMessageSource;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.http.HttpHeaders;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenExceptionHandler;
import tbot.common.utils.MessageUtils;
import tbot.modules.security.dao.SysUserTokenDao;
import tbot.modules.security.entity.SysUserTokenEntity;
import tbot.modules.security.service.CaptchaService;
import tbot.modules.security.service.impl.SysUserTokenServiceImpl;
import tbot.modules.sys.dto.SysUserDTO;
import tbot.modules.sys.service.SysDictDataService;
import tbot.modules.sys.service.SysParamsService;
import tbot.modules.sys.service.SysUserService;

class ProxyAuthMvcTest {
    private static final String TOKEN = "offline-mvc-token";
    private static final long USER_ID = 7L;

    private SysUserTokenDao tokenDao;
    private SysUserService userService;
    private SysParamsService paramsService;
    private MockMvc mvc;
    private MessageSource previousMessageSource;

    @BeforeEach
    void setUp() {
        previousMessageSource = (MessageSource) ReflectionTestUtils.getField(MessageUtils.class, "messageSource");
        StaticMessageSource messages = new StaticMessageSource();
        messages.setUseCodeAsDefaultMessage(true);
        ReflectionTestUtils.setField(MessageUtils.class, "messageSource", messages);

        tokenDao = mock(SysUserTokenDao.class);
        userService = mock(SysUserService.class);
        paramsService = mock(SysParamsService.class);
        SysUserTokenServiceImpl tokenService = new SysUserTokenServiceImpl(userService);
        ReflectionTestUtils.setField(tokenService, "baseDao", tokenDao);
        LoginController controller = new LoginController(userService, tokenService,
                mock(CaptchaService.class), paramsService, mock(SysDictDataService.class));

        // Real MVC dispatch/advice/response conversion without application or database startup.
        mvc = MockMvcBuilders.standaloneSetup(controller)
                .setControllerAdvice(new RenExceptionHandler())
                .alwaysDo(print())
                .build();
    }

    @AfterEach
    void tearDown() {
        ReflectionTestUtils.setField(MessageUtils.class, "messageSource", previousMessageSource);
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {" ", "Basic offline", "bearer offline", "Bearer", "Bearer ", "Bearer   "})
    void malformedOrMissingHeaderReturns401WithoutLookup(String authorization) throws Exception {
        MockHttpServletRequestBuilder request = get("/user/proxy-auth");
        if (authorization != null) {
            request.header(HttpHeaders.AUTHORIZATION, authorization);
        }
        mvc.perform(request).andExpect(status().isUnauthorized()).andExpect(content().string(""));
        verifyNoInteractions(tokenDao, userService);
    }

    @ParameterizedTest
    @ValueSource(strings = {"Bearer offline-mvc-token", "Bearer   offline-mvc-token  "})
    void activeSuperAdminReturns204AndPasswordRemainsRedacted(String authorization) throws Exception {
        validToken();
        SysUserDTO user = activeUser();
        when(userService.getByUserId(USER_ID)).thenReturn(user);

        mvc.perform(get("/user/proxy-auth").header(HttpHeaders.AUTHORIZATION, authorization))
                .andExpect(status().isNoContent()).andExpect(content().string(""));
        assertEquals("", user.getPassword());
        verify(tokenDao).getByToken(TOKEN);
    }

    @ParameterizedTest
    @NullSource
    @ValueSource(ints = {0, 2})
    void inactiveOrMissingStatusReturns401(Integer userStatus) throws Exception {
        validToken();
        SysUserDTO user = activeUser();
        user.setStatus(userStatus);
        when(userService.getByUserId(USER_ID)).thenReturn(user);
        assertAuthStatus(401);
    }

    @ParameterizedTest
    @NullSource
    @ValueSource(ints = {0, 2})
    void nonSuperAdminOrMissingRoleReturns403(Integer superAdmin) throws Exception {
        validToken();
        SysUserDTO user = activeUser();
        user.setSuperAdmin(superAdmin);
        when(userService.getByUserId(USER_ID)).thenReturn(user);
        assertAuthStatus(403);
    }

    @Test
    void missingTokenReturns401() throws Exception {
        assertAuthStatus(401);
        verifyNoInteractions(userService);
    }

    @Test
    void expiredTokenReturns401BeforeUserLookup() throws Exception {
        SysUserTokenEntity token = validToken();
        token.setExpireDate(new Date(0));
        assertAuthStatus(401);
        verifyNoInteractions(userService);
    }

    @Test
    void nullExpiryReturns401BeforeUserLookup() throws Exception {
        SysUserTokenEntity token = validToken();
        token.setExpireDate(null);
        assertAuthStatus(401);
        verifyNoInteractions(userService);
    }

    @Test
    void orphanedTokenReturns401ThroughRealServiceAndAdvice() throws Exception {
        validToken();
        when(userService.getByUserId(USER_ID)).thenReturn(null);
        assertAuthStatus(401);
        verify(userService).getByUserId(USER_ID);
    }

    @Test
    void incompleteTokenIdentityFailsClosedBeforeUserLookup() throws Exception {
        SysUserTokenEntity token = validToken();
        token.setUserId(null);
        assertAuthStatus(401);
        verifyNoInteractions(userService);
    }

    @ParameterizedTest
    @MethodSource("lookupFailures")
    void tokenLookupFailureReturns503RatherThanSuccessfulAdviceBody(RuntimeException failure) throws Exception {
        when(tokenDao.getByToken(TOKEN)).thenThrow(failure);
        assertAuthStatus(503);
        verifyNoInteractions(userService);
    }

    @ParameterizedTest
    @MethodSource("lookupFailures")
    void userLookupFailureReturns503RatherThanSuccessfulAdviceBody(RuntimeException failure) throws Exception {
        validToken();
        when(userService.getByUserId(USER_ID)).thenThrow(failure);
        assertAuthStatus(503);
    }

    @Test
    void unrelatedEndpointKeepsExistingGlobalResultContract() throws Exception {
        when(paramsService.getValueObject(org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.eq(Boolean.class)))
                .thenThrow(new IllegalStateException("offline unrelated lookup failure"));
        mvc.perform(get("/user/pub-config"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(ErrorCode.INTERNAL_SERVER_ERROR));
    }

    private static Stream<RuntimeException> lookupFailures() {
        return Stream.of(new DataAccessResourceFailureException("offline DAO unavailable"),
                new IllegalStateException("offline unexpected lookup failure"));
    }

    private void assertAuthStatus(int expected) throws Exception {
        mvc.perform(get("/user/proxy-auth").header(HttpHeaders.AUTHORIZATION, "Bearer " + TOKEN))
                .andExpect(status().is(expected)).andExpect(content().string(""));
    }

    private SysUserTokenEntity validToken() {
        SysUserTokenEntity token = new SysUserTokenEntity();
        token.setId(11L);
        token.setToken(TOKEN);
        token.setUserId(USER_ID);
        token.setExpireDate(new Date(System.currentTimeMillis() + 60_000));
        when(tokenDao.getByToken(TOKEN)).thenReturn(token);
        return token;
    }

    private SysUserDTO activeUser() {
        SysUserDTO user = new SysUserDTO();
        user.setId(USER_ID);
        user.setStatus(1);
        user.setSuperAdmin(1);
        user.setPassword("offline-password-hash");
        return user;
    }
}
