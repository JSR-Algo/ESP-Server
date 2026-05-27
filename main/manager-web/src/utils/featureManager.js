//Function configTool
import Api from "@/apis/api";
import store from "@/store";

class FeatureManager {
    constructor() {
        this.defaultFeatures = {
            voiceprintRecognition: {
                name: 'feature.voiceprintRecognition.name',
                enabled: false,
                description: 'feature.voiceprintRecognition.description'
            },
            voiceClone: {
                name: 'feature.voiceClone.name',
                enabled: false,
                description: 'feature.voiceClone.description'
            },
            knowledgeBase: {
                name: 'feature.knowledgeBase.name',
                enabled: false,
                description: 'feature.knowledgeBase.description'
            },
            mcpAccessPoint: {
                name: 'feature.mcpAccessPoint.name',
                enabled: false,
                description: 'feature.mcpAccessPoint.description'
            },
            vad: {
                name: 'feature.vad.name',
                enabled: false,
                description: 'feature.vad.description'
            },
            asr: {
                name: 'feature.asr.name',
                enabled: false,
                description: 'feature.asr.description'
            }
        };
        this.currentFeatures = { ...this.defaultFeatures }; // Config in current memory
        this.initialized = false;
        this.initPromise = null;
    }

    /**
     * Wait init complete
     */
    async waitForInitialization() {
        if (!this.initPromise) {
            this.initPromise = this.init();
        }
        await this.initPromise;
        return this.initialized;
    }

    /**
     * InitializeFunction config
     */
    async init() {
        try {
            // frompub-configAPI get config
            const config = await this.getConfigFromPubConfig();
            if (config) {
                this.currentFeatures = { ...config }; // SaveTo memory
                this.initialized = true;
                return;
            }
        } catch (error) {
            console.warn('Failed to get config from pub-config API:', error);
        }

        // pub-configAPI failed, use default config
        this.currentFeatures = { ...this.defaultFeatures }; // SaveDefault config to memory
        this.initialized = true;
    }

    /**
     * UpdateconfigCache
     */
    updateConfigCache(config) {
        store.commit('setPubConfig', config);
        localStorage.setItem('pubConfig', JSON.stringify(config));
    }

    /**
     * frompub-configAPI get config
     */
    async getConfigFromPubConfig() {
        return new Promise((resolve) => {
            // Call directlypub-configAPI get config
            Api.user.getPubConfig((result) => {
                // Check return result structure
                if (result && result.status === 200) {
                    // Check whether hasdataField
                    if (result.data) {
                        const configCache = result.data.data || {};
                        // Check whether hascodeField, if exists then followcodeDetermine
                        if (result.data.code !== undefined) {
                            if (result.data.code === 0 && result.data.data && result.data.data.systemWebMenu) {
                                try {
                                    let config;
                                    if (typeof result.data.data.systemWebMenu === 'string') {
                                        // If string, need parseJSON
                                        config = JSON.parse(result.data.data.systemWebMenu);
                                    } else {
                                        // If already object, use directly
                                        config = result.data.data.systemWebMenu;
                                    }

                                    // Check config containsfeaturesObject
                                    if (config && config.features) {
                                        // EnsureknowledgeBaseFunction exists and config correct
                                        if (!config.features.knowledgeBase) {
                                            console.warn('knowledgeBase feature missing in config, merge default config');
                                            config.features = { ...this.defaultFeatures, ...config.features };
                                        }
                                        resolve(config.features);
                                    } else {
                                        console.warn('features object missing in config, using default config');
                                        resolve(this.defaultFeatures);
                                    }
                                    configCache.systemWebMenu = config;
                                } catch (error) {
                                    console.warn('Failed to handle systemWebMenu config:', error);
                                    resolve(null);
                                }
                            } else {
                                console.warn('API returned code not 0 or lacks required data, using default config');
                                resolve(null);
                            }
                        } else {
                            // If nonecodeField, check directlysystemWebMenu
                            if (result.data && result.data.systemWebMenu) {
                                try {
                                    let config;
                                    if (typeof result.data.systemWebMenu === 'string') {
                                        // If string, need parseJSON
                                        config = JSON.parse(result.data.systemWebMenu);
                                    } else {
                                        // If already object, use directly
                                        config = result.data.systemWebMenu;
                                    }

                                    // Check config containsfeaturesObject
                                    if (config && config.features) {
                                        // EnsureknowledgeBaseFunction exists and config correct
                                        if (!config.features.knowledgeBase) {
                                            console.warn('knowledgeBase feature missing in config, merge default config');
                                            config.features = { ...this.defaultFeatures, ...config.features };
                                        }
                                        resolve(config.features);
                                    } else {
                                        console.warn('features object missing in config, using default config');
                                        resolve(this.defaultFeatures);
                                    }
                                    configCache.systemWebMenu = config;
                                } catch (error) {
                                    console.warn('Failed to handle systemWebMenu config:', error);
                                    resolve(null);
                                }
                            } else {
                                console.warn('Interface response missing systemWebMenu data, use default config');
                                resolve(null);
                            }
                        }
                        this.updateConfigCache(configCache)
                    } else {
                        console.warn('data field missing in API returned data, using default config');
                        resolve(null);
                    }
                } else {
                    console.warn('pub-config API call failed, using default config');
                    resolve(null);
                }
            });
        });
    }

    /**
     * Get current config
     */
    getCurrentConfig() {
        // Return current config in memory
        return this.currentFeatures;
    }

    /**
     * SaveConfig to backendAPI
     */
    async saveConfig(config) {
        try {
            // Update config in memory
            this.currentFeatures = { ...config };

            // AsyncSaveTo backendAPI
            this.saveConfigToAPI(config).catch(error => {
                console.warn('Failed to save config to API:', error);
            }).finally(() => {
                this.init()
            });

            // Trigger config changeEvent
            window.dispatchEvent(new CustomEvent('featureConfigChanged', {
                detail: config
            }));
        } catch (error) {
            console.error('Save feature config failed:', error);
        }
    }

    /**
     * SaveConfig to backendAPI
     */
    async saveConfigToAPI(config) {
        return new Promise((resolve) => {
            // Use known directlyID(600) update parameters
            Api.admin.updateParam(
                {
                    id: 600,
                    paramCode: 'system-web.menu',
                    paramValue: JSON.stringify({
                        features: config,
                        groups: {
                            featureManagement: ["voiceprintRecognition", "voiceClone", "knowledgeBase", "mcpAccessPoint"],
                            voiceManagement: ["vad", "asr"]
                        }
                    }),
                    valueType: 'json',
                    remark: 'System feature menu config'
                },
                (updateResult) => {
                    if (updateResult.code === 0) {
                        resolve();
                    } else {
                        // If update fails, may be parameter missing or otherError, log but not blockSavetolocalStorage
                        console.warn('Update parameter failed:', updateResult.msg);
                        resolve(); // Not blockSavetolocalStorage
                    }
                },
                (error) => {
                    console.warn('Update parameter failed:', error);
                    resolve(); // Not blockSavetolocalStorage
                }
            );
        });
    }



    /**
     * Get allFunction config
     */
    getAllFeatures() {
        return this.getCurrentConfig();
    }

    /**
     * Get simplified config object (for homepage component)
     */
    getConfig() {
        const features = this.getAllFeatures();
        return {
            voiceprintRecognition: features.voiceprintRecognition?.enabled || false,
            voiceClone: features.voiceClone?.enabled || false,
            knowledgeBase: features.knowledgeBase?.enabled || false,
            mcpAccessPoint: features.mcpAccessPoint?.enabled || false,
            vad: features.vad?.enabled || false,
            asr: features.asr?.enabled || false
        };
    }

    /**
     * Get specified functionStatus
     */
    getFeatureStatus(featureKey) {
        const features = this.getAllFeatures();
        return features[featureKey]?.enabled || false;
    }

    /**
     * Set functionStatus
     */
    setFeatureStatus(featureKey, enabled) {
        const features = this.getAllFeatures();
        if (features[featureKey]) {
            features[featureKey].enabled = enabled;
            this.saveConfig(features);
            return true;
        }
        return false;
    }

    /**
     * EnableFunction
     */
    enableFeature(featureKey) {
        return this.setFeatureStatus(featureKey, true);
    }

    /**
     * DisableFunction
     */
    disableFeature(featureKey) {
        return this.setFeatureStatus(featureKey, false);
    }

    /**
     * Toggle functionStatus
     */
    toggleFeature(featureKey) {
        const currentStatus = this.getFeatureStatus(featureKey);
        return this.setFeatureStatus(featureKey, !currentStatus);
    }

    /**
     * Reset all functions to defaultStatus
     */
    resetToDefault() {
        this.saveConfig(this.defaultFeatures);
    }

    /**
     * Batch update featuresStatus
     */
    updateFeatures(featureUpdates) {
        const features = this.getAllFeatures();
        Object.keys(featureUpdates).forEach(featureKey => {
            if (features[featureKey]) {
                features[featureKey].enabled = featureUpdates[featureKey];
            }
        });
        this.saveConfig(features);
    }

    /**
     * Get alreadyEnablefunction list
     */
    getEnabledFeatures() {
        const features = this.getAllFeatures();
        return Object.keys(features).filter(key => features[key].enabled);
    }

    /**
     * Check functionEnabled
     */
    isFeatureEnabled(featureKey) {
        return this.getFeatureStatus(featureKey);
    }
}

// Create singleton instance
const featureManager = new FeatureManager();

export default featureManager;