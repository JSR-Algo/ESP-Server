package tbot.common.utils;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

public class AESUtilsTest {

    @Test
    public void testEncryptAndDecrypt() {
        String key = "tbot1234567890";
        String plainText = "Hello, 小智!";

        System.out.println("原始文本: " + plainText);
        System.out.println("密钥: " + key);

        // 加密
        String encrypted = AESUtils.encrypt(key, plainText);
        System.out.println("加密结果: " + encrypted);

        // 解密
        String decrypted = AESUtils.decrypt(key, encrypted);
        System.out.println("解密结果: " + decrypted);

        // 验证
        assertEquals(plainText, decrypted, "加解密结果应该一致");
        System.out.println("加解密一致性: " + plainText.equals(decrypted));
    }

    @Test
    public void testDifferentKeyLengths() {
        String[] keys = {
                "1234567890123456", // 16位
                "123456789012345678901234", // 24位
                "12345678901234567890123456789012", // 32位
                "short", // 短密钥
                "verylongkeythatwillbetruncatedto32bytes" // 长密钥
        };

        String plainText = "测试文本";

        for (String key : keys) {
            String encrypted = AESUtils.encrypt(key, plainText);
            String decrypted = AESUtils.decrypt(key, encrypted);
            assertEquals(plainText, decrypted, "密钥长度: " + key.length());
        }
    }

    @Test
    public void testSpecialCharacters() {
        String key = "tbot1234567890";
        String[] testTexts = {
                "Hello World",
                "你好世界",
                "Hello, 小智!",
                "特殊字符: !@#$%^&*()",
                "数字123和中文混合",
                "Emoji: 😀🎉🚀",
                "空字符串测试",
                ""
        };

        for (String text : testTexts) {
            String encrypted = AESUtils.encrypt(key, text);
            String decrypted = AESUtils.decrypt(key, encrypted);
            assertEquals(text, decrypted, "测试文本: " + text);
        }
    }

    @Test
    public void testCrossLanguageCompatibility() {
        // 这些是Python版本生成的加密结果，用于测试跨语言兼容性
        String key = "tbot1234567890";
        String plainText = "Hello, 小智!";

        // Python版本生成的加密结果（需要运行Python测试后获取）
        // String pythonEncrypted = "从Python测试中获取的加密结果";
        // String decrypted = AESUtils.decrypt(key, pythonEncrypted);
        // assertEquals(plainText, decrypted, "Java应该能解密Python加密的结果");

        // 生成Java加密结果供Python测试
        String javaEncrypted = AESUtils.encrypt(key, plainText);
        System.out.println("Java加密结果供Python测试: " + javaEncrypted);
    }
}