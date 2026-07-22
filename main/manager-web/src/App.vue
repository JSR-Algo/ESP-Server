<template>
  <div id="app">
    <router-view />
    <nest-login-dialog
      v-if="!nestAuthDisabled"
      ref="nestLogin"
      @logged-in="onNestLoggedIn"
    />
    <cache-viewer v-if="isCDNEnabled" :visible.sync="showCacheViewer" />
  </div>
</template>

<style lang="scss">
#app {
  font-family: Avenir, Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-align: center;
  color: #2c3e50;
}

nav {
  padding: 30px;

  a {
    font-weight: bold;
    color: #2c3e50;

    &.router-link-exact-active {
      color: #42b983;
    }
  }
}

.copyright {
  padding: 0 !important;
  color: rgb(0, 0, 0);
  font-size: 12px;
  font-weight: 400;
  margin-top: auto;
  width: 100%;
  height: 100%;
  display: flex;
  justify-content: center;
  align-items: center;
}

.el-message {
  top: 70px !important;
}
</style>
<script>
import CacheViewer from '@/components/CacheViewer.vue';
import NestLoginDialog from '@/components/NestLoginDialog.vue';
import { logCacheStatus } from '@/utils/cacheViewer';
import { isNestAuthDisabled } from '@/utils/nestAuthModeCore.mjs';

export default {
  name: 'App',
  components: {
    CacheViewer,
    NestLoginDialog,
  },
  data() {
    return {
      showCacheViewer: false,
      nestAuthDisabled: isNestAuthDisabled(),
      isCDNEnabled: process.env.VUE_APP_USE_CDN === 'true'
    };
  },
  created() {
    // Mount store Status
    this.$store.commit('setUserInfo', JSON.parse(localStorage.getItem('userInfo') || '{}'));
    this.$store.commit('setPubConfig', JSON.parse(localStorage.getItem('pubConfig') || '{}'));
  },
  mounted() {
    // Course/Learner/Monitoring pages use NestJS admin auth (separate from manager-api).
    // nestHttp emits this when a /nestjs request gets 401 so we can prompt Author sign-in
    // without logging the user out of the manager console.
    if (!this.nestAuthDisabled) {
      window.addEventListener('tbot:nest-auth-required', this.openNestLogin);
    }

    // Check whether mobile device andVUE_APP_H5_URLNot empty, if both conditions met then jump toH5Page
    if (this.isMobileDevice() && process.env.VUE_APP_H5_URL) {
      window.location.href = process.env.VUE_APP_H5_URL;
      return;
    }
    
    // Only WhenEnableCDNAdd related only whenEventand Feature
    if (this.isCDNEnabled) {
      // Add global shortcutAlt+CUsed to display cache viewer
      document.addEventListener('keydown', this.handleKeyDown);

      // Add cache check method on global object for debugging
      window.checkCDNCacheStatus = () => {
        this.showCacheViewer = true;
      };

      // Output in consolePromptInfo
      console.info(
        '%c[' + this.$t('system.name') + '] ' + this.$t('cache.cdnEnabled'),
        'color: #409EFF; font-weight: bold;'
      );
      console.info(
        'Press Alt+C shortcut or run checkCDNCacheStatus() in console to view CDN cache status'
      );

      // CheckService WorkerStatus
      this.checkServiceWorkerStatus();
    } else {
      console.info(
        '%c[' + this.$t('system.name') + '] ' + this.$t('cache.cdnDisabled'),
        'color: #67C23A; font-weight: bold;'
      );
    }
  },
  beforeDestroy() {
    if (!this.nestAuthDisabled) {
      window.removeEventListener('tbot:nest-auth-required', this.openNestLogin);
    }
    // Only WhenEnableCDNRemove only whenEventListen
    if (this.isCDNEnabled) {
      document.removeEventListener('keydown', this.handleKeyDown);
    }
  },
  methods: {
    openNestLogin() {
      if (this.nestAuthDisabled) return;
      if (this.$refs.nestLogin && typeof this.$refs.nestLogin.open === 'function') {
        this.$refs.nestLogin.open();
      }
    },
    onNestLoggedIn() {
      // Reload current view so course/device/monitoring fetches retry with the new token.
      this.$router.go(0);
    },
    handleKeyDown(e) {
      // Alt+C Shortcut Key
      if (e.altKey && e.key === 'c') {
        this.showCacheViewer = true;
      }
    },
    isMobileDevice() {
      // Function to detect whether mobile device
      return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
    },
    
    async checkServiceWorkerStatus() {
      // CheckService WorkerAlreadyRegister
      if ('serviceWorker' in navigator) {
        try {
          const registrations = await navigator.serviceWorker.getRegistrations();
          if (registrations.length > 0) {
            console.info(
              '%c[' + this.$t('system.name') + '] ' + this.$t('cache.serviceWorkerRegistered'),
              'color: #67C23A; font-weight: bold;'
            );

            // Output cacheStatusTo console
            setTimeout(async () => {
              const hasCaches = await logCacheStatus();
              if (!hasCaches) {
                console.info(
                '%c[' + this.$t('system.name') + '] ' + this.$t('cache.noCacheDetected'),
                'color: #E6A23C; font-weight: bold;'
              );

              // Provide extra in dev environmentPrompt
              if (process.env.NODE_ENV === 'development') {
                console.info(
                  '%c[' + this.$t('system.name') + '] ' + this.$t('cache.swDevEnvWarning'),
                  'color: #E6A23C; font-weight: bold;'
                );
                console.info(this.$t('cache.swCheckMethods'));
                console.info('1. ' + this.$t('cache.swCheckMethod1'));
                console.info('2. ' + this.$t('cache.swCheckMethod2'));
                console.info('3. ' + this.$t('cache.swCheckMethod3'));
              }
              }
            }, 2000);
          } else {
            console.info(
                  '%c[' + this.$t('system.name') + '] ' + this.$t('cache.serviceWorkerNotRegistered'),
                  'color: #F56C6C; font-weight: bold;'
                );

                if (process.env.NODE_ENV === 'development') {
                  console.info(
                    '%c[' + this.$t('system.name') + '] ' + this.$t('cache.swDevEnvNormal'),
                    'color: #E6A23C; font-weight: bold;'
                  );
                  console.info(this.$t('cache.swProdOnly'));
                  console.info(this.$t('cache.swTestingTitle'));
                  console.info('1. ' + this.$t('cache.swTestingStep1'));
                  console.info('2. ' + this.$t('cache.swTestingStep2'));
                }
          }
        } catch (error) {
          console.error('Failed to check Service Worker status:', error);
        }
      } else {
          console.warn(this.$t('cache.swNotSupported'));
        }
    }
  }
};
</script>
