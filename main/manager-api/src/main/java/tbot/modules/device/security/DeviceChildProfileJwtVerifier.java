package tbot.modules.device.security;

import static java.nio.charset.StandardCharsets.UTF_8;

import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.time.Clock;
import java.time.Instant;
import java.util.Base64;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

@Component
public class DeviceChildProfileJwtVerifier {
    private static final String REQUIRED_SCOPE = "device:child-profile:sync";

    private final PublicKey publicKey;
    private final String issuer;
    private final String audience;
    private final long maxTtlSeconds;
    private final Clock clock;
    private final ObjectMapper objectMapper;

    @Autowired
    public DeviceChildProfileJwtVerifier(
            @Value("${tbot.internal.child-profile-jwt.public-key:}") String publicKeyPem,
            @Value("${tbot.internal.child-profile-jwt.issuer:}") String issuer,
            @Value("${tbot.internal.child-profile-jwt.audience:}") String audience,
            @Value("${tbot.internal.child-profile-jwt.max-ttl-seconds:300}") long maxTtlSeconds,
            ObjectMapper objectMapper) {
        this(publicKeyPem, issuer, audience, maxTtlSeconds, Clock.systemUTC(), objectMapper);
    }

    public DeviceChildProfileJwtVerifier(String publicKeyPem, String issuer, String audience,
            long maxTtlSeconds, Clock clock, ObjectMapper objectMapper) {
        this.publicKey = parsePublicKey(publicKeyPem);
        this.issuer = issuer;
        this.audience = audience;
        this.maxTtlSeconds = maxTtlSeconds;
        this.clock = clock;
        this.objectMapper = objectMapper;
    }

    public void verify(String authorization, String pathDeviceId) {
        try {
            if (publicKey == null) {
                reject(JwtRejection.VERIFIER_UNAVAILABLE);
            }
            if (authorization == null || !authorization.startsWith("Bearer ")) {
                reject(JwtRejection.MISSING_AUTHORIZATION);
            }
            String[] parts = authorization.substring(7).split("\\.", -1);
            if (parts.length != 3) {
                reject(JwtRejection.MALFORMED_TOKEN);
            }
            Base64.Decoder decoder = Base64.getUrlDecoder();
            JsonNode header = objectMapper.readTree(decoder.decode(parts[0]));
            if (!"RS256".equals(header.path("alg").asText())) {
                reject(JwtRejection.INVALID_ALGORITHM);
            }
            Signature signature = Signature.getInstance("SHA256withRSA");
            signature.initVerify(publicKey);
            signature.update((parts[0] + "." + parts[1]).getBytes(UTF_8));
            if (!signature.verify(decoder.decode(parts[2]))) {
                reject(JwtRejection.INVALID_SIGNATURE);
            }

            JsonNode claims = objectMapper.readTree(decoder.decode(parts[1]));
            Instant now = clock.instant();
            long issuedAt = requiredLong(claims, "iat");
            long notBefore = requiredLong(claims, "nbf");
            long expiry = requiredLong(claims, "exp");
            if (!issuer.equals(claims.path("iss").asText()) || !audienceMatches(claims.path("aud"))) {
                reject(JwtRejection.INVALID_AUTHORITY);
            }
            if (!pathDeviceId.equals(claims.path("deviceId").asText())) {
                reject(JwtRejection.DEVICE_BINDING_MISMATCH);
            }
            if (!claims.path("scope").isTextual()
                    || !REQUIRED_SCOPE.equals(claims.path("scope").textValue())) {
                reject(JwtRejection.INVALID_SCOPE);
            }
            long nowSeconds = now.getEpochSecond();
            long maximumExpiry;
            try {
                maximumExpiry = Math.addExact(issuedAt, maxTtlSeconds);
            } catch (ArithmeticException exception) {
                maximumExpiry = Long.MAX_VALUE;
            }
            if (issuedAt > nowSeconds || notBefore > nowSeconds || expiry <= nowSeconds
                    || expiry <= issuedAt || expiry > maximumExpiry) {
                reject(JwtRejection.INVALID_LIFETIME);
            }
        } catch (JwtRejectionException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new JwtRejectionException(JwtRejection.MALFORMED_TOKEN);
        }
    }

    private static void reject(JwtRejection reason) {
        throw new JwtRejectionException(reason);
    }

    private boolean audienceMatches(JsonNode claim) {
        if (claim.isTextual()) {
            return audience.equals(claim.asText());
        }
        if (claim.isArray()) {
            return claim.size() == 1 && audience.equals(claim.get(0).asText());
        }
        return false;
    }

    private static long requiredLong(JsonNode claims, String name) {
        JsonNode value = claims.get(name);
        if (value == null || !value.isIntegralNumber() || !value.canConvertToLong()) {
            reject(JwtRejection.INVALID_LIFETIME);
        }
        return value.longValue();
    }

    private static PublicKey parsePublicKey(String pem) {
        if (pem == null || pem.isBlank()) {
            return null;
        }
        try {
            String encoded = pem.replace("-----BEGIN PUBLIC KEY-----", "")
                    .replace("-----END PUBLIC KEY-----", "")
                    .replaceAll("\\s", "");
            return KeyFactory.getInstance("RSA").generatePublic(
                    new X509EncodedKeySpec(Base64.getDecoder().decode(encoded)));
        } catch (Exception exception) {
            throw new IllegalArgumentException("invalid child-profile JWT public key", exception);
        }
    }

    public enum JwtRejection {
        PATH_MISMATCH,
        VERIFIER_UNAVAILABLE,
        MISSING_AUTHORIZATION,
        MALFORMED_TOKEN,
        INVALID_ALGORITHM,
        INVALID_SIGNATURE,
        INVALID_AUTHORITY,
        DEVICE_BINDING_MISMATCH,
        INVALID_SCOPE,
        INVALID_LIFETIME
    }

    public static final class JwtRejectionException extends SecurityException {
        private final JwtRejection reason;

        public JwtRejectionException(JwtRejection reason) {
            super(reason.name());
            this.reason = reason;
        }

        public JwtRejection reason() {
            return reason;
        }
    }
}
