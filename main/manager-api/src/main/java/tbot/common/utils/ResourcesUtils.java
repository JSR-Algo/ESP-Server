package tbot.common.utils;

import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.core.io.ResourceLoader;
import org.springframework.core.io.Resource;
import org.springframework.stereotype.Component;
import tbot.common.exception.RenException;
import tbot.common.exception.ErrorCode;


import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;

/**
 * Resource handling tool
 */
@AllArgsConstructor
@Slf4j
@Component
public class ResourcesUtils {
    private ResourceLoader resourceLoader;

    /**
     * Read resource, return string
     * @param fileName Resource path:resourcesStart under
     * @return String
     */
    public String loadString(String fileName)  {
        Resource resource = resourceLoader.getResource("classpath:" + fileName);
        StringBuilder luaScriptBuilder = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(resource.getInputStream()))) {
            String line;
            while ((line = reader.readLine()) != null) {
                luaScriptBuilder.append(line).append("\n");
            }
        }  catch (IOException e){
            log.error("Method: loadString() failed to read resource--{}",e.getMessage());
            throw new RenException(ErrorCode.RESOURCE_READ_ERROR);
        }
        return luaScriptBuilder.toString();
    }
}
