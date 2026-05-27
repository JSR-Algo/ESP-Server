/**
 * Cache view tool - Used to checkCDNWhether resource alreadyService WorkerCache
 */

/**
 * Get allService WorkerCachedName
 * @returns {Promise<string[]>} CacheNameList
 */
export const getCacheNames = async () => {
  if (!('caches' in window)) {
    return [];
  }
  
  try {
    return await caches.keys();
  } catch (error) {
    console.error('Get cache name failed:', error);
    return [];
  }
};

/**
 * Get all in specified cacheURL
 * @param {string} cacheName CacheName
 * @returns {Promise<string[]>} CachedURLList
 */
export const getCacheUrls = async (cacheName) => {
  if (!('caches' in window)) {
    return [];
  }
  
  try {
    const cache = await caches.open(cacheName);
    const requests = await cache.keys();
    return requests.map(request => request.url);
  } catch (error) {
    console.error(`Failed to get URLs from cache ${cacheName}:`, error);
    return [];
  }
};

/**
 * Check specificURLWhether cached
 * @param {string} url To checkURL
 * @returns {Promise<boolean>} WhetherCached
 */
export const isUrlCached = async (url) => {
  if (!('caches' in window)) {
    return false;
  }
  
  try {
    const cacheNames = await getCacheNames();
    for (const cacheName of cacheNames) {
      const cache = await caches.open(cacheName);
      const match = await cache.match(url);
      if (match) {
        return true;
      }
    }
    return false;
  } catch (error) {
    console.error(`Failed to check whether URL ${url} is cached:`, error);
    return false;
  }
};

/**
 * Get all current pageCDNResource cacheStatus
 * @returns {Promise<Object>} CacheStatusObject
 */
export const checkCdnCacheStatus = async () => {
  // fromCDNFind resource in cache
  const cdnCaches = ['cdn-stylesheets', 'cdn-scripts'];
  const results = {
    css: [],
    js: [],
    totalCached: 0,
    totalNotCached: 0
  };
  
  for (const cacheName of cdnCaches) {
    try {
      const urls = await getCacheUrls(cacheName);
      
      // DistinguishCSSandJSResource
      for (const url of urls) {
        if (url.endsWith('.css')) {
          results.css.push({ url, cached: true });
        } else if (url.endsWith('.js')) {
          results.js.push({ url, cached: true });
        }
        results.totalCached++;
      }
    } catch (error) {
      console.error(`Failed to get ${cacheName} cache info:`, error);
    }
  }
  
  return results;
};

/**
 * Clear allService WorkerCache
 * @returns {Promise<boolean>} Whether cleared successfully
 */
export const clearAllCaches = async () => {
  if (!('caches' in window)) {
    return false;
  }
  
  try {
    const cacheNames = await getCacheNames();
    for (const cacheName of cacheNames) {
      await caches.delete(cacheName);
    }
    return true;
  } catch (error) {
    console.error('Clear all caches failed:', error);
    return false;
  }
};

/**
 * CacheStatusOutput to console
 */
export const logCacheStatus = async () => {
  console.group('Service Worker cache status');
  
  const cacheNames = await getCacheNames();
  console.log('Discovered caches:', cacheNames);
  
  for (const cacheName of cacheNames) {
    const urls = await getCacheUrls(cacheName);
    console.group(`Cache: ${cacheName} (${urls.length} item)`);
    urls.forEach(url => console.log(url));
    console.groupEnd();
  }
  
  console.groupEnd();
  return cacheNames.length > 0;
}; 