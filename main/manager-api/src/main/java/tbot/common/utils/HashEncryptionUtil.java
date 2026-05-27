package tbot.common.utils;

import lombok.extern.slf4j.Slf4j;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/**
 * Utility class for hash encryption algorithm
 * @author zjy
 */
@Slf4j
public class HashEncryptionUtil {
    /**
     * Usemd5Encrypt
     * @param context EncryptedContent
     * @return Hash value
     */
    public static String Md5hexDigest(String context){
        return hexDigest(context,"MD5");
    }

    /**
     * Encrypt with specified hash algorithm
     * @param context EncryptedContent
     * @param algorithm Hash Algorithm
     * @return Hash value
     */
   public static String hexDigest(String context,String algorithm ){
       // GetMD5Algorithm Instance
       MessageDigest md = null;
       try {
           md = MessageDigest.getInstance(algorithm);
       } catch (NoSuchAlgorithmException e) {
           log.error("Encryption failed algorithm: {}",algorithm);
           throw new RuntimeException("Encryption failed,"+ algorithm +"Hash algorithm not supported by system");
       }
       // CalculateAgent idofMD5value
       byte[] messageDigest = md.digest(context.getBytes());
       // Convert byte array to hex string
       StringBuilder hexString = new StringBuilder();
       for (byte b : messageDigest) {
           String hex = Integer.toHexString(0xFF & b);
           if (hex.length() == 1) {
               hexString.append('0');
           }
           hexString.append(hex);
       }
       return hexString.toString();
   }

}
