package tbot.modules.knowledge.rag;

import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

import lombok.extern.slf4j.Slf4j;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;

/**
 * Knowledge base adapter factory class
 * Responsible for creating and managing different types of knowledge basesAPIAdapter
 */
@Slf4j
public class KnowledgeBaseAdapterFactory {

    // RegisterAdapter type mapping
    private static final Map<String, Class<? extends KnowledgeBaseAdapter>> adapterRegistry = new HashMap<>();

    // Adapter instance cache
    private static final Map<String, KnowledgeBaseAdapter> adapterCache = new ConcurrentHashMap<>();

    // Max cache instance count, prevent memory leak (Issue 9)
    private static final int MAX_CACHE_SIZE = 50;

    static {
        // RegisterBuilt-in adapter type
        registerAdapter("ragflow", tbot.modules.knowledge.rag.impl.RAGFlowAdapter.class);
        // Can hereRegisterMore adapter types
    }

    /**
     * RegisterNew adapter type
     * 
     * @param adapterType  Adapter type ID
     * @param adapterClass Adapter Class
     */
    public static void registerAdapter(String adapterType, Class<? extends KnowledgeBaseAdapter> adapterClass) {
        if (adapterRegistry.containsKey(adapterType)) {
            log.warn("Adapter type '{}' Already exists, will be overwritten", adapterType);
        }
        adapterRegistry.put(adapterType, adapterClass);
        log.info("RegisterAdapter type: {} -> {}", adapterType, adapterClass.getSimpleName());
    }

    /**
     * Get adapter instance
     * 
     * @param adapterType Adapter type
     * @param config      Config Parameters
     * @return Adapter instance
     */
    public static KnowledgeBaseAdapter getAdapter(String adapterType, Map<String, Object> config) {
        String cacheKey = buildCacheKey(adapterType, config);

        // Check whether instance already exists in cache
        if (adapterCache.containsKey(cacheKey)) {
            log.debug("Get adapter instance from cache: {}", cacheKey);
            return adapterCache.get(cacheKey);
        }

        // Create new adapter instance
        KnowledgeBaseAdapter adapter = createAdapter(adapterType, config);

        // Cache adapter instance (With capacity limit check)
        if (adapterCache.size() >= MAX_CACHE_SIZE) {
            log.warn("Adapter cache reached limit ({})execute memory protective clear", MAX_CACHE_SIZE);
            // Simple handling: clear directly. In production, LRU is recommended
            adapterCache.clear();
        }

        adapterCache.put(cacheKey, adapter);
        log.info("Create and cache adapter instance: {}", cacheKey);

        return adapter;
    }

    /**
     * Get adapter instance (no config)
     * 
     * @param adapterType Adapter type
     * @return Adapter instance
     */
    public static KnowledgeBaseAdapter getAdapter(String adapterType) {
        return getAdapter(adapterType, null);
    }

    /**
     * Get all existingRegisteradapter type
     * 
     * @return Adapter type collection
     */
    public static Set<String> getRegisteredAdapterTypes() {
        return adapterRegistry.keySet();
    }

    /**
     * Check whether adapter type alreadyRegister
     * 
     * @param adapterType Adapter type
     * @return AlreadyRegister
     */
    public static boolean isAdapterTypeRegistered(String adapterType) {
        return adapterRegistry.containsKey(adapterType);
    }

    /**
     * Clear adapter cache
     */
    public static void clearCache() {
        int cacheSize = adapterCache.size();
        adapterCache.clear();
        log.info("Clear adapter cache, total cleared {} Instances", cacheSize);
    }

    /**
     * Remove cache for specific adapter type
     * 
     * @param adapterType Adapter type
     */
    public static void removeCacheByType(String adapterType) {
        int removedCount = 0;
        for (String cacheKey : adapterCache.keySet()) {
            if (cacheKey.startsWith(adapterType + "@")) {
                adapterCache.remove(cacheKey);
                removedCount++;
            }
        }
        log.info("Remove adapter type '{}' Cache, total removed {} Instances", adapterType, removedCount);
    }

    /**
     * Get adapter factoryStatusInfo
     * 
     * @return StatusInfo
     */
    public static Map<String, Object> getFactoryStatus() {
        Map<String, Object> status = new HashMap<>();
        status.put("registeredAdapterTypes", adapterRegistry.keySet());
        status.put("cachedAdapterCount", adapterCache.size());
        status.put("cacheKeys", adapterCache.keySet());
        return status;
    }

    /**
     * Create adapter instance
     * 
     * @param adapterType Adapter type
     * @param config      Config Parameters
     * @return Adapter instance
     */
    private static KnowledgeBaseAdapter createAdapter(String adapterType, Map<String, Object> config) {
        if (!adapterRegistry.containsKey(adapterType)) {
            throw new RenException(ErrorCode.RAG_ADAPTER_TYPE_NOT_SUPPORTED,
                    "Unsupported adapter type: " + adapterType);
        }

        try {
            Class<? extends KnowledgeBaseAdapter> adapterClass = adapterRegistry.get(adapterType);
            KnowledgeBaseAdapter adapter = adapterClass.getDeclaredConstructor().newInstance();

            // Initialize adapter
            if (config != null) {
                adapter.initialize(config);

                // Validate Config
                if (!adapter.validateConfig(config)) {
                    throw new RenException(ErrorCode.RAG_CONFIG_VALIDATION_FAILED,
                            "Adapter config validation failed: " + adapterType);
                }
            }

            log.info("Adapter instance created successfully: {}", adapterType);
            return adapter;

        } catch (Exception e) {
            log.error("Failed to create adapter instance: {}", adapterType, e);
            throw new RenException(ErrorCode.RAG_ADAPTER_CREATION_FAILED,
                    "Create adapter failed: " + adapterType + ", Error: " + e.getMessage());
        }
    }

    /**
     * Build cache key
     * 
     * @param adapterType Adapter type
     * @param config      Config Parameters
     * @return Cache key
     */
    private static String buildCacheKey(String adapterType, Map<String, Object> config) {
        if (config == null || config.isEmpty()) {
            return adapterType + "@default";
        }

        // Generate cache key based on config parameters
        StringBuilder keyBuilder = new StringBuilder(adapterType + "@");

        // Use config hash as part of cache key
        int configHash = config.hashCode();
        keyBuilder.append(configHash);

        return keyBuilder.toString();
    }
}