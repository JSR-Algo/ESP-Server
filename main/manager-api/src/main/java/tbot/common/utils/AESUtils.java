package tbot.common.utils;

import java.nio.charset.StandardCharsets;
import java.util.Base64;

import javax.crypto.Cipher;
import javax.crypto.spec.SecretKeySpec;

public class AESUtils {

    private static final String ALGORITHM = "AES";
    private static final String TRANSFORMATION = "AES/ECB/PKCS5Padding";

    /**
     * AESEncrypt
     * 
     * @param key       Key(16bit,24Bits or32bit)
     * @param plainText String to encrypt
     * @return EncryptedBase64String
     */
    public static String encrypt(String key, String plainText) {
        try {
            // EnsureKeyLength is16,24or32position
            byte[] keyBytes = padKey(key.getBytes(StandardCharsets.UTF_8));
            SecretKeySpec secretKey = new SecretKeySpec(keyBytes, ALGORITHM);

            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.ENCRYPT_MODE, secretKey);

            byte[] encryptedBytes = cipher.doFinal(plainText.getBytes(StandardCharsets.UTF_8));
            return Base64.getEncoder().encodeToString(encryptedBytes);
        } catch (Exception e) {
            throw new RuntimeException("AES encryption failed", e);
        }
    }

    /**
     * AESDecrypt
     * 
     * @param key           Key(16bit,24Bits or32bit)
     * @param encryptedText To DecryptBase64String
     * @return Decrypted string
     */
    public static String decrypt(String key, String encryptedText) {
        try {
            // EnsureKeyLength is16,24or32position
            byte[] keyBytes = padKey(key.getBytes(StandardCharsets.UTF_8));
            SecretKeySpec secretKey = new SecretKeySpec(keyBytes, ALGORITHM);

            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.DECRYPT_MODE, secretKey);

            byte[] encryptedBytes = Base64.getDecoder().decode(encryptedText);
            byte[] decryptedBytes = cipher.doFinal(encryptedBytes);
            return new String(decryptedBytes, StandardCharsets.UTF_8);
        } catch (Exception e) {
            throw new RuntimeException("AES decryption failed", e);
        }
    }

    /**
     * FillKeyto specified length(16,24or32bit)
     * 
     * @param keyBytes OriginalKeyByte Array
     * @return PaddedKeyByte Array
     */
    private static byte[] padKey(byte[] keyBytes) {
        int keyLength = keyBytes.length;
        if (keyLength == 16 || keyLength == 24 || keyLength == 32) {
            return keyBytes;
        }

        // IfKeyLength insufficient, use0padding; if exceeds, truncate first32position
        byte[] paddedKey = new byte[32];
        System.arraycopy(keyBytes, 0, paddedKey, 0, Math.min(keyLength, 32));
        return paddedKey;
    }
}
