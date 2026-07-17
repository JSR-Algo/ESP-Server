package tbot.modules.device;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.Signature;
import java.math.BigInteger;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Base64;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

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
    @Test void rejectsRequiredScopeAlongsideExtraScope() throws Exception { assertRejected(token(keyPair, "issuer", "manager", "device:child-profile:sync other", "device-1", NOW.plusSeconds(30), NOW.minusSeconds(1)), "device-1"); }
    @Test void rejectsScopeWithSurroundingWhitespace() throws Exception { assertRejected(token(keyPair, "issuer", "manager", " device:child-profile:sync ", "device-1", NOW.plusSeconds(30), NOW.minusSeconds(1)), "device-1"); }
    @Test void rejectsWrongDeviceBinding() throws Exception { assertRejected(token(keyPair, "issuer", "manager", "device:child-profile:sync", "device-2", NOW.plusSeconds(30), NOW.minusSeconds(1)), "device-1"); }
    @Test void rejectsExpiredToken() throws Exception { assertRejected(token(keyPair, "issuer", "manager", "device:child-profile:sync", "device-1", NOW.minusSeconds(1), NOW.minusSeconds(30)), "device-1"); }
    @Test void rejectsNotBeforeInFuture() throws Exception { assertRejected(token(keyPair, "issuer", "manager", "device:child-profile:sync", "device-1", NOW.plusSeconds(30), NOW.plusSeconds(1)), "device-1"); }
    @Test void acceptsExactMaximumTtl() throws Exception { assertDoesNotThrow(() -> verifier().verify("Bearer " + tokenWithTimes(keyPair, NOW.minusSeconds(2).getEpochSecond(), NOW.minusSeconds(1).getEpochSecond(), NOW.plusSeconds(298).getEpochSecond()), "device-1")); }
    @Test void rejectsMaximumTtlPlusOne() throws Exception { assertRejected(tokenWithTimes(keyPair, NOW.minusSeconds(2).getEpochSecond(), NOW.minusSeconds(1).getEpochSecond(), NOW.plusSeconds(299).getEpochSecond()), "device-1"); }
    @Test void rejectsExtremeLifetimeWithoutOverflow() throws Exception { assertRejected(tokenWithTimes(keyPair, Long.MIN_VALUE, NOW.minusSeconds(1).getEpochSecond(), Long.MAX_VALUE), "device-1"); }
    @Test void rejectsReversedExtremeLifetimeWithoutOverflow() throws Exception { assertRejected(tokenWithTimes(keyPair, Long.MAX_VALUE, NOW.minusSeconds(1).getEpochSecond(), Long.MIN_VALUE), "device-1"); }

    @ParameterizedTest(name = "rejects {0} outside signed long range: {1}")
    @MethodSource("oversizedNumericDates")
    void rejectsIntegralNumericDatesOutsideSignedLongRange(String claim, String direction,
            String issuedAt, String notBefore, String expiry) throws Exception {
        assertRejected(tokenWithRawTimes(keyPair, issuedAt, notBefore, expiry), "device-1");
    }

    private static java.util.stream.Stream<Arguments> oversizedNumericDates() {
        BigInteger modulus = BigInteger.ONE.shiftLeft(64);
        String iat = Long.toString(NOW.minusSeconds(2).getEpochSecond());
        String nbf = Long.toString(NOW.minusSeconds(1).getEpochSecond());
        String exp = Long.toString(NOW.plusSeconds(30).getEpochSecond());
        return java.util.stream.Stream.of(
                Arguments.of("iat", "above", modulus.add(new BigInteger(iat)).toString(), nbf, exp),
                Arguments.of("iat", "below", new BigInteger(iat).subtract(modulus).toString(), nbf, exp),
                Arguments.of("nbf", "above", iat, modulus.add(new BigInteger(nbf)).toString(), exp),
                Arguments.of("nbf", "below", iat, new BigInteger(nbf).subtract(modulus).toString(), exp),
                Arguments.of("exp", "above", iat, nbf, modulus.add(new BigInteger(exp)).toString()),
                Arguments.of("exp", "below", iat, nbf, new BigInteger(exp).subtract(modulus).toString()));
    }

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

    private static String tokenWithTimes(KeyPair signer, long issuedAt, long notBefore, long expiry) throws Exception {
        return tokenWithRawTimes(signer, Long.toString(issuedAt), Long.toString(notBefore), Long.toString(expiry));
    }

    private static String tokenWithRawTimes(KeyPair signer, String issuedAt, String notBefore, String expiry) throws Exception {
        Base64.Encoder encoder = Base64.getUrlEncoder().withoutPadding();
        String header = encoder.encodeToString("{\"alg\":\"RS256\",\"typ\":\"JWT\"}".getBytes(UTF_8));
        String payloadJson = "{\"iss\":\"issuer\",\"aud\":\"manager\",\"scope\":\"device:child-profile:sync\",\"deviceId\":\"device-1\",\"iat\":" + issuedAt + ",\"nbf\":" + notBefore + ",\"exp\":" + expiry + "}";
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
