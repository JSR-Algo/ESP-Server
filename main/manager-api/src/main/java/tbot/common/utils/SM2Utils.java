package tbot.common.utils;

import org.bouncycastle.asn1.gm.GMNamedCurves;
import org.bouncycastle.asn1.x9.X9ECParameters;
import org.bouncycastle.crypto.engines.SM2Engine;
import org.bouncycastle.crypto.params.ECDomainParameters;
import org.bouncycastle.crypto.params.ECPrivateKeyParameters;
import org.bouncycastle.crypto.params.ECPublicKeyParameters;
import org.bouncycastle.crypto.params.ParametersWithRandom;
import org.bouncycastle.jcajce.provider.asymmetric.ec.BCECPrivateKey;
import org.bouncycastle.jcajce.provider.asymmetric.ec.BCECPublicKey;
import org.bouncycastle.jce.provider.BouncyCastleProvider;
import org.bouncycastle.math.ec.ECPoint;
import org.bouncycastle.util.encoders.Hex;

import java.math.BigInteger;
import java.security.*;
import java.security.spec.ECGenParameterSpec;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

/**
 * SM2Encryption utility class (uses hex format, withchancheng-archive-serviceKeep consistent with project)
 */
public class SM2Utils {

    /**
     * Public Key Constant
     */
    public static final String KEY_PUBLIC_KEY = "publicKey";
    /**
     * Private key return value constant
     */
    public static final String KEY_PRIVATE_KEY = "privateKey";

    static {
        Security.addProvider(new BouncyCastleProvider());
    }

    /**
     * SM2Encryption Algorithm
     *
     * @param publicKey Hex public key
     * @param data      Plaintext Data
     * @return Hex ciphertext
     */
    public static String encrypt(String publicKey, String data) {
        try {
            // Get OneSM2Curve Parameters
            X9ECParameters sm2ECParameters = GMNamedCurves.getByName("sm2p256v1");
            // ConstructECCAlgorithm parameters, curve equation, elliptic curveGPoint, big integerN
            ECDomainParameters domainParameters = new ECDomainParameters(sm2ECParameters.getCurve(), sm2ECParameters.getG(), sm2ECParameters.getN());
            //Extract public key point
            ECPoint pukPoint = sm2ECParameters.getCurve().decodePoint(Hex.decode(publicKey));
            // Before public key02Or03Indicates compressed public key,04Means uncompressed public key, 04when, can remove leading04
            ECPublicKeyParameters publicKeyParameters = new ECPublicKeyParameters(pukPoint, domainParameters);

            SM2Engine sm2Engine = new SM2Engine(SM2Engine.Mode.C1C3C2);
            // Setsm2For encryption mode
            sm2Engine.init(true, new ParametersWithRandom(publicKeyParameters, new SecureRandom()));

            byte[] in = data.getBytes(StandardCharsets.UTF_8);
            byte[] arrayOfBytes = sm2Engine.processBlock(in, 0, in.length);
            return Hex.toHexString(arrayOfBytes);
        } catch (Exception e) {
            throw new RuntimeException("SM2 encryption failed", e);
        }
    }

    /**
     * SM2Decryption Algorithm
     *
     * @param privateKey Hex private key
     * @param cipherData Hex ciphertext data
     * @return Plaintext
     */
    public static String decrypt(String privateKey, String cipherData) {
        try {
            // UseBCCiphertext during library encryption/decryption uses04At start, passed ciphertext lacks prefix04Then append
            if (!cipherData.startsWith("04")) {
                cipherData = "04" + cipherData;
            }
            byte[] cipherDataByte = Hex.decode(cipherData);
            BigInteger privateKeyD = new BigInteger(privateKey, 16);
            //Get OneSM2Curve Parameters
            X9ECParameters sm2ECParameters = GMNamedCurves.getByName("sm2p256v1");
            //ConstructdomainParameter
            ECDomainParameters domainParameters = new ECDomainParameters(sm2ECParameters.getCurve(), sm2ECParameters.getG(), sm2ECParameters.getN());
            ECPrivateKeyParameters privateKeyParameters = new ECPrivateKeyParameters(privateKeyD, domainParameters);

            SM2Engine sm2Engine = new SM2Engine(SM2Engine.Mode.C1C3C2);
            // Setsm2For decryption mode
            sm2Engine.init(false, privateKeyParameters);

            byte[] arrayOfBytes = sm2Engine.processBlock(cipherDataByte, 0, cipherDataByte.length);
            return new String(arrayOfBytes, StandardCharsets.UTF_8);
        } catch (Exception e) {
            throw new RuntimeException("SM2 decryption failed", e);
        }
    }

    /**
     * GenerateKeypair
     */
    public static Map<String, String> createKey() {
        try {
            ECGenParameterSpec sm2Spec = new ECGenParameterSpec("sm2p256v1");
            // Get elliptic curve typeKeyFor Generator
            KeyPairGenerator kpg = KeyPairGenerator.getInstance("EC", new BouncyCastleProvider());
            // UseSM2Parameter init generator
            kpg.initialize(sm2Spec);
            // GetKeypair
            KeyPair keyPair = kpg.generateKeyPair();
            PublicKey publicKey = keyPair.getPublic();
            BCECPublicKey p = (BCECPublicKey) publicKey;
            PrivateKey privateKey = keyPair.getPrivate();
            BCECPrivateKey s = (BCECPrivateKey) privateKey;
            
            Map<String, String> result = new HashMap<>();
            result.put(KEY_PUBLIC_KEY, Hex.toHexString(p.getQ().getEncoded(false)));
            result.put(KEY_PRIVATE_KEY, Hex.toHexString(s.getD().toByteArray()));
            return result;
        } catch (Exception e) {
            throw new RuntimeException("Failed to generate SM2 key pair", e);
        }
    }


}