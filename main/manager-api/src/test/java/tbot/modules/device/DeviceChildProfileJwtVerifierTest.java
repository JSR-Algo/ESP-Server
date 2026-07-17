package tbot.modules.device;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.Signature;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Base64;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import com.fasterxml.jackson.databind.ObjectMapper;

import tbot.modules.device.security.DeviceChildProfileJwtVerifier;

class DeviceChildProfileJwtVerifierTest {
    private static final Instant NOW = Instant.parse("2026-07-17T10:00:00Z");
    private static KeyPair keyPair;
    private static KeyPair otherKeyPair;
    private DeviceChildProfileJwtVerifier verifier;

    @BeforeAll
    static void keys() throws Exception {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
        generator.initialize(2048);
        keyPair = generator.generateKeyPair();
        otherKeyPair = generator.generateKeyPair();
    }

    @Test
    void acceptsValidBoundServiceJwt() throws Exception {
        verifier = verifier();
        assertDoesNotThrow(() -> verifier.verify("Bearer " + token(keyPair, "issuer", "manager", "device:child-profile:sync", "device-1", NOW.plusSeconds(30), NOW.minusSeconds(1)), "device-1"));
    }

    @Test void rejectsWrongSignature() throws Exception { assertRejected(token(otherKeyPair, "issuer", "manager", "device:child-profile:sync", "device-1", NOW.plusSeconds(30), NOW.minusSeconds(1)), "device-1"); }
    @Test void rejectsWrongIssuer() throws Exception { assertRejected(token(keyPair, "other", "manager", "device:child-profile:sync", "device-1", NOW.plusSeconds(30), NOW.minusSeconds(1)), "device-1"); }
    @Test void rejectsWrongAudience() throws Exception { assertRejected(token(keyPair, "issuer", "other", "device:child-profile:sync", "device-1", NOW.plusSeconds(30), NOW.minusSeconds(1)), "device-1"); }
    @Test void rejectsMissingScope() throws Exception { assertRejected(token(keyPair, "issuer", "manager", "other", "device-1", NOW.plusSeconds(30), NOW.minusSeconds(1)), "device-1"); }
    @Test void rejectsWrongDeviceBinding() throws Exception { assertRejected(token(keyPair, "issuer", "manager", "device:child-profile:sync", "device-2", NOW.plusSeconds(30), NOW.minusSeconds(1)), "device-1"); }
    @Test void rejectsExpiredToken() throws Exception { assertRejected(token(keyPair, "issuer", "manager", "device:child-profile:sync", "device-1", NOW.minusSeconds(1), NOW.minusSeconds(30)), "device-1"); }
    @Test void rejectsNotBeforeInFuture() throws Exception { assertRejected(token(keyPair, "issuer", "manager", "device:child-profile:sync", "device-1", NOW.plusSeconds(30), NOW.plusSeconds(1)), "device-1"); }

    private void assertRejected(String token, String deviceId) {
        verifier = verifier();
        assertThrows(SecurityException.class, () -> verifier.verify("Bearer " + token, deviceId));
    }

    private static DeviceChildProfileJwtVerifier verifier() {
        return new DeviceChildProfileJwtVerifier(pem(keyPair), "issuer", "manager", 300, Clock.fixed(NOW, ZoneOffset.UTC), new ObjectMapper());
    }

    private static String token(KeyPair signer, String issuer, String audience, String scope, String deviceId, Instant expiry, Instant notBefore) throws Exception {
        Base64.Encoder encoder = Base64.getUrlEncoder().withoutPadding();
        String header = encoder.encodeToString("{\"alg\":\"RS256\",\"typ\":\"JWT\"}".getBytes(UTF_8));
        long issuedAt = NOW.minusSeconds(2).getEpochSecond();
        String payloadJson = "{\"iss\":\"" + issuer + "\",\"aud\":\"" + audience + "\",\"scope\":\"" + scope + "\",\"deviceId\":\"" + deviceId + "\",\"iat\":" + issuedAt + ",\"nbf\":" + notBefore.getEpochSecond() + ",\"exp\":" + expiry.getEpochSecond() + "}";
        String payload = encoder.encodeToString(payloadJson.getBytes(UTF_8));
        String signingInput = header + "." + payload;
        Signature signature = Signature.getInstance("SHA256withRSA");
        signature.initSign(signer.getPrivate());
        signature.update(signingInput.getBytes(UTF_8));
        return signingInput + "." + encoder.encodeToString(signature.sign());
    }

    private static String pem(KeyPair pair) {
        return "-----BEGIN PUBLIC KEY-----\n" + Base64.getMimeEncoder(64, "\n".getBytes(UTF_8)).encodeToString(pair.getPublic().getEncoded()) + "\n-----END PUBLIC KEY-----";
    }
}
