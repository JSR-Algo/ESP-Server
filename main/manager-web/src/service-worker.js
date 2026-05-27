/* global self, workbox */

// CustomService WorkerInstall and activation handling logic
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// CDNResource list
const CDN_CSS = [
  'https://unpkg.com/element-ui@2.15.14/lib/theme-chalk/index.css',
  'https://cdnjs.cloudflare.com/ajax/libs/normalize/8.0.1/normalize.min.css'
];

const CDN_JS = [
  'https://unpkg.com/vue@2.6.14/dist/vue.min.js',
  'https://unpkg.com/vue-router@3.6.5/dist/vue-router.min.js',
  'https://unpkg.com/vuex@3.6.2/dist/vuex.min.js',
  'https://unpkg.com/element-ui@2.15.14/lib/index.js',
  'https://unpkg.com/axios@0.27.2/dist/axios.min.js',
  'https://unpkg.com/opus-decoder@0.7.7/dist/opus-decoder.min.js'
];

// whenService WorkerInjectedmanifestAuto execute after
const manifest = self.__WB_MANIFEST || [];

// CheckEnabledCDNMode
const isCDNEnabled = manifest.some(entry => 
  entry.url === 'cdn-mode' && entry.revision === 'enabled'
);

console.log(`Service Worker Initialized, CDNMode: ${isCDNEnabled ? 'Enable' : 'Disable'}`);

// InjectworkboxRelated code
importScripts('https://storage.googleapis.com/workbox-cdn/releases/7.0.0/workbox-sw.js');
workbox.setConfig({ debug: false });

// Enableworkbox
workbox.core.skipWaiting();
workbox.core.clientsClaim();

// Precache offline page
const OFFLINE_URL = '/offline.html';
workbox.precaching.precacheAndRoute([
  { url: OFFLINE_URL, revision: null }
]);

// Add install completeEventHandler, show installation in consoleMessage
self.addEventListener('install', event => {
  if (isCDNEnabled) {
    console.log('Service Worker installed, start caching CDN resources');
  } else {
    console.log('Service Worker installed, CDN mode disabled, only local resources cached');
  }
  
  // Ensure offline page cached
  event.waitUntil(
    caches.open('offline-cache').then((cache) => {
      return cache.add(OFFLINE_URL);
    })
  );
});

// Add activationEventHandler
self.addEventListener('activate', event => {
  console.log('Service Worker activated, now controlling page');
  
  // Clean old version cache
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.filter(cacheName => {
          // Clean cache except current version
          return cacheName.startsWith('workbox-') && !workbox.core.cacheNames.runtime.includes(cacheName);
        }).map(cacheName => {
          return caches.delete(cacheName);
        })
      );
    })
  );
});

// AddfetchEventInterceptor, used to viewCDNWhether resource hits cache
self.addEventListener('fetch', event => {
  // OnlyEnableCDNOnly in modeCDNResource cache monitor
  if (isCDNEnabled) {
    const url = new URL(event.request.url);
    
    // ForCDNResource, output whether cache hitInfo
    if ([...CDN_CSS, ...CDN_JS].includes(url.href)) {
      // Do not interfere normalfetchFlow, only add logs
      console.log(`Request CDN resource: ${url.href}`);
    }
  }
});

// Only atCDNCache in modeCDNResource
if (isCDNEnabled) {
  // CacheCDNofCSSResource
  workbox.routing.registerRoute(
    ({ url }) => CDN_CSS.includes(url.href),
    new workbox.strategies.CacheFirst({
      cacheName: 'cdn-stylesheets',
      plugins: [
        new workbox.expiration.ExpirationPlugin({
          maxAgeSeconds: 365 * 24 * 60 * 60, // Add to1Year cache
          maxEntries: 10, // Max cache10countCSSFile
        }),
        new workbox.cacheableResponse.CacheableResponsePlugin({
          statuses: [0, 200], // Cache succeededResponse
        }),
      ],
    })
  );

  // CacheCDNofJSResource
  workbox.routing.registerRoute(
    ({ url }) => CDN_JS.includes(url.href),
    new workbox.strategies.CacheFirst({
      cacheName: 'cdn-scripts',
      plugins: [
        new workbox.expiration.ExpirationPlugin({
          maxAgeSeconds: 365 * 24 * 60 * 60, // Add to1Year cache
          maxEntries: 20, // Max cache20countJSFile
        }),
        new workbox.cacheableResponse.CacheableResponsePlugin({
          statuses: [0, 200], // Cache succeededResponse
        }),
      ],
    })
  );
}

// RegardlessEnabledCDNMode, cache local static resources
workbox.routing.registerRoute(
  /\.(?:js|css|png|jpg|jpeg|svg|gif|ico|woff|woff2|eot|ttf|otf)$/,
  new workbox.strategies.StaleWhileRevalidate({
    cacheName: 'static-resources',
    plugins: [
      new workbox.expiration.ExpirationPlugin({
        maxAgeSeconds: 7 * 24 * 60 * 60, // 7Day cache
        maxEntries: 50, // Max cache50files
      }),
    ],
  })
);

// CacheHTMLPage
workbox.routing.registerRoute(
  /\.html$/,
  new workbox.strategies.NetworkFirst({
    cacheName: 'html-cache',
    plugins: [
      new workbox.expiration.ExpirationPlugin({
        maxAgeSeconds: 1 * 24 * 60 * 60, // 1Day cache
        maxEntries: 10, // Max cache10countHTMLFile
      }),
    ],
  })
);

// Offline page - Use more reliable handling method
workbox.routing.setCatchHandler(async ({ event }) => {
  // Return appropriate default page based on request type
  switch (event.request.destination) {
    case 'document':
      // If web request, return offline page
      return caches.match(OFFLINE_URL);
    default:
      // All other requests returnError
      return Response.error();
  }
}); 