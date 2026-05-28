package tbot.common.utils;

import java.nio.charset.StandardCharsets;
import java.util.Base64;

import javax.crypto.Cipher;
import javax.crypto.spec.SecretKeySpec;

public class AESUtils {

    private static final String ALGORITHM = "AES";
    // TODO: Migrate from AES/ECB/PKCS5Padding to AES/GCM/NoPadding with random IV.
    // CRITICAL: This requires re-encrypting all existing encrypted data.
    // Current ECB mode leaks patterns in plaintext and should not be used for new data.
    private static final String TRANSFORMATION = "AES/ECB/PKCS5Padding";

    /**
     * AES Encrypt
     * 
     * @param key       Key (16, 24 or 32 bytes)
     * @param plainText String to encrypt
     * @return Encrypted Base64 string
     */
    public static String encrypt(String key, String plainText) {
        try {
            // Ensure key length is 16, 24 or 32 bytes
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
     * AES Decrypt
     * 
     * @param key           Key (16, 24 or 32 bytes)
     * @param encryptedText To decrypt Base64 string
     * @return Decrypted string
     */
    public static String decrypt(String key, String encryptedText) {
        try {
            // Ensure key length is 16, 24 or 32 bytes
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
     * Pad key to specified length (16, 24 or 32 bytes)
     * 
     * @param keyBytes Original key byte array
     * @return Padded key byte array
     */
    private static byte[] padKey(byte[] keyBytes) {
        int keyLength = keyBytes.length;
        if (keyLength == 16 || keyLength == 24 || keyLength == 32) {
            return keyBytes;
        }

        // If key length insufficient, use 0 padding; if exceeds, truncate to first 32 bytes
        byte[] paddedKey = new byte[32];
        System.arraycopy(keyBytes, 0, paddedKey, 0, Math.min(keyLength, 32));
        return paddedKey;
    }
}
