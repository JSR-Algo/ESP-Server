package tbot.modules.sys.utils;

import java.net.URI;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.regex.Pattern;

import org.apache.commons.lang3.StringUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.socket.WebSocketHttpHeaders;
import org.springframework.web.socket.client.WebSocketClient;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;

public class WebSocketValidator {
    private static final Logger logger = LoggerFactory.getLogger(WebSocketValidator.class);

    // WebSocket URLRegular expression
    private static final Pattern WS_URL_PATTERN = Pattern
            .compile("^wss?://[\\w.-]+(?:\\.[\\w.-]+)*(?::\\d+)?(?:/[\\w.-]*)*$");

    /**
     * VerifyWebSocketAddress Format
     * 
     * @param url WebSocketAddress
     * @return Valid
     */
    public static boolean validateUrlFormat(String url) {
        if (StringUtils.isBlank(url)) {
            return false;
        }
        return WS_URL_PATTERN.matcher(url.trim()).matches();
    }

    /**
     * TestWebSocketConnect
     * 
     * @param url WebSocketAddress
     * @return Connectable
     */
    public static boolean testConnection(String url) {
        if (!validateUrlFormat(url)) {
            return false;
        }

        try {
            WebSocketClient client = new StandardWebSocketClient();
            CompletableFuture<Boolean> future = new CompletableFuture<>();
            WebSocketHttpHeaders headers = new WebSocketHttpHeaders();

            client.execute(new WebSocketTestHandler(future), headers, URI.create(url));

            // Wait Max5Get connection result in seconds
            return future.get(5, TimeUnit.SECONDS);
        } catch (Exception e) {
            logger.error("WebSocket connection test failed: {}", url, e);
            return false;
        }
    }
}