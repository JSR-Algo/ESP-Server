/* eslint-disable no-console */

import {
  scheduleAuthoringSafeCallback,
  scheduleServiceWorkerActivation,
} from './utils/serviceWorkerUpdateSafety.mjs';

let controllerReloaded = false;

function activateWaitingWorker(registration) {
  if (registration && registration.waiting) {
    scheduleServiceWorkerActivation(registration.waiting);
  }
}

export const register = () => {
  if (process.env.NODE_ENV === 'production' && 'serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      const swUrl = `${process.env.BASE_URL}service-worker.js`;

      navigator.serviceWorker.addEventListener('controllerchange', () => {
        if (controllerReloaded) {
          return;
        }
        controllerReloaded = true;
        scheduleAuthoringSafeCallback(() => window.location.reload());
      });
      
      console.info(`[TBOT] Trying to register Service Worker, URL: ${swUrl}`);
      
      // Check firstService WorkerAlreadyRegister
      navigator.serviceWorker.getRegistrations().then(registrations => {
        if (registrations.length > 0) {
          console.info('[TBOT] Existing Service Worker registration found, checking for updates');
        }
        
        // ContinueRegisterService Worker
        navigator.serviceWorker
          .register(swUrl, { updateViaCache: 'none' })
          .then(registration => {
            console.info('[TBOT] Service Worker registered successfully');

            activateWaitingWorker(registration);
            registration.update().catch(error => {
              console.warn('[TBOT] Service Worker update check failed:', error);
            });
            
            // Update handling
            registration.onupdatefound = () => {
              const installingWorker = registration.installing;
              if (installingWorker == null) {
                return;
              }
              installingWorker.onstatechange = () => {
                if (installingWorker.state === 'installed') {
                  if (navigator.serviceWorker.controller) {
                    console.log('[TBOT] New content available, activating Service Worker');
                    scheduleServiceWorkerActivation(installingWorker);
                  } else {
                    // All normal,Service WorkerSuccessfully installed
                    console.log('[TBOT] Content cached for offline use');
                    
                    // Can initialize cache here
                    setTimeout(() => {
                      // preHotCDNCache
                      const cdnUrls = [
                        'https://unpkg.com/element-ui@2.15.14/lib/theme-chalk/index.css',
                        'https://cdnjs.cloudflare.com/ajax/libs/normalize/8.0.1/normalize.min.css',
                        'https://unpkg.com/vue@2.6.14/dist/vue.min.js',
                        'https://unpkg.com/vue-router@3.6.5/dist/vue-router.min.js',
                        'https://unpkg.com/vuex@3.6.2/dist/vuex.min.js',
                        'https://unpkg.com/element-ui@2.15.14/lib/index.js',
                        'https://unpkg.com/axios@0.27.2/dist/axios.min.js',
                        'https://unpkg.com/opus-decoder@0.7.7/dist/opus-decoder.min.js'
                      ];
                      
                      // preHotCache
                      cdnUrls.forEach(url => {
                        fetch(url, { mode: 'no-cors' }).catch(err => {
                          console.log(`Preheat cache ${url} failed`, err);
                        });
                      });
                    }, 2000);
                  }
                }
              };
            };
          })
          .catch(error => {
            console.error('Service Worker registration failed:', error);
            
            if (error.name === 'TypeError' && error.message.includes('Failed to register a ServiceWorker')) {
              console.warn('[TBOT] Network error registering Service Worker, CDN resources may not be cached');
              if (process.env.NODE_ENV === 'production') {
                console.info(
                  'Possible causes: 1. Server MIME type not configured correctly 2. Server SSL certificate issue 3. Server did not return service-worker.js file'
                );
              }
            }
          });
      });
    });
  }
};

export const unregister = () => {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.ready
      .then(registration => {
        registration.unregister();
      })
      .catch(error => {
        console.error(error.message);
      });
  }
};
