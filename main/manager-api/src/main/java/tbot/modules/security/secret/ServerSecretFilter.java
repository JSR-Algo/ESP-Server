package tbot.modules.security.secret;

import static java.nio.charset.StandardCharsets.UTF_8;

import java.io.IOException;
import java.security.MessageDigest;

import org.apache.commons.lang3.StringUtils;
import org.apache.shiro.web.filter.authc.AuthenticatingFilter;
import org.springframework.web.bind.annotation.RequestMethod;

import jakarta.servlet.ServletRequest;
import jakarta.servlet.ServletResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.utils.HttpContextUtils;
import tbot.common.utils.JsonUtils;
import tbot.common.utils.Result;
import tbot.modules.sys.service.SysParamsService;

/**
 * Config API Filter
 */
@Slf4j
@RequiredArgsConstructor
public class ServerSecretFilter extends AuthenticatingFilter {
    private final SysParamsService sysParamsService;

    @Override
    protected ServerSecretToken createToken(ServletRequest request, ServletResponse response) {
        // Get Requesttoken
        String token = getRequestToken((HttpServletRequest) request);

        if (StringUtils.isBlank(token)) {
            log.warn("createToken:token is empty");
            return null;
        }

        return new ServerSecretToken(token);
    }

    @Override
    protected boolean isAccessAllowed(ServletRequest request, ServletResponse response, Object mappedValue) {
        // pairOPTIONSRequest Pass
        if (((HttpServletRequest) request).getMethod().equals(RequestMethod.OPTIONS.name())) {
            return true;
        }
        return false;
    }

    @Override
    protected boolean onAccessDenied(ServletRequest servletRequest, ServletResponse servletResponse) throws Exception {
        // GettokenAnd validate
        String token = getRequestToken((HttpServletRequest) servletRequest);
        if (StringUtils.isBlank(token)) {
            // tokenis empty, return401
            this.sendUnauthorizedResponse((HttpServletResponse) servletResponse, "Server key cannot be empty");
            return false;
        }

        // VerifytokenMatches
        String serverSecret = getServerSecret();
        if (StringUtils.isBlank(serverSecret)
                || !MessageDigest.isEqual(serverSecret.getBytes(UTF_8), token.getBytes(UTF_8))) {
            // tokenInvalid, return401
            this.sendUnauthorizedResponse((HttpServletResponse) servletResponse, "Invalid server key");
            return false;
        }

        return true;
    }

    /**
     * Send unauthorizedResponse
     */
    private void sendUnauthorizedResponse(HttpServletResponse response, String message) {
        response.setContentType("application/json;charset=utf-8");
        response.setHeader("Access-Control-Allow-Credentials", "true");
        response.setHeader("Access-Control-Allow-Origin", HttpContextUtils.getOrigin());

        try {
            String json = JsonUtils.toJsonString(new Result<Void>().error(ErrorCode.UNAUTHORIZED, message));
            response.getWriter().print(json);
        } catch (IOException e) {
            log.error("Response output failed", e);
        }
    }

    /**
     * Get requesttoken
     */
    private String getRequestToken(HttpServletRequest httpRequest) {
        String token = null;
        // fromheaderGet intoken
        String authorization = httpRequest.getHeader("Authorization");
        if (StringUtils.isNotBlank(authorization) && authorization.startsWith("Bearer ")) {
            token = authorization.replace("Bearer ", "");
        }
        return token;
    }

    private String getServerSecret() {
        return sysParamsService.getValue(Constant.SERVER_SECRET, true);
    }
}
