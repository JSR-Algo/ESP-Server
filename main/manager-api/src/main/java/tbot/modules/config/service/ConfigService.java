package tbot.modules.config.service;

import java.util.List;
import java.util.Map;

public interface ConfigService {
    /**
     * Get server config
     *
     * @param isCache Cache
     * @return ConfigInfo
     */
    Object getConfig(Boolean isCache);

    /**
     * Get agent modelConfig
     *
     * @param macAddress     MAC address
     * @param selectedModule Models instantiated by client
     * @return Model configInfo
     */
    Map<String, Object> getAgentModels(String macAddress, Map<String, String> selectedModule);

    /**
     * Get agent replacement words
     *
     * @param macAddress Device MAC address
     * @return Replacement wordlist, format like ["Template1|Template01", "Template2|Template02"]
     */
    List<String> getCorrectWords(String macAddress);
}