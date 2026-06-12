import 'element-ui/lib/theme-chalk/index.css';
import 'normalize.css/normalize.css'; // A modern alternative to CSS resets
import Vue from 'vue';
import ElementUI from 'element-ui';
import ElementLocale from 'element-ui/lib/locale';
import elEnLocale from 'element-ui/lib/locale/lang/en';
import elViLocale from 'element-ui/lib/locale/lang/vi';
import elZhCnLocale from 'element-ui/lib/locale/lang/zh-CN';
import elZhTwLocale from 'element-ui/lib/locale/lang/zh-TW';
import App from './App.vue';
import router from './router';
import store from './store';
import i18n from './i18n';
import './styles/global.scss';
import { register as registerServiceWorker } from './registerServiceWorker';
import featureManager from './utils/featureManager';

// Create event bus for component communication
Vue.prototype.$eventBus = new Vue();

Vue.use(ElementUI);

// Element UI built-in component text (el-popconfirm / message-box buttons, table
// empty, pagination, etc.) defaulted to Chinese. Make it follow the app language
// by merging Element UI's locale packs into the vue-i18n messages and routing
// Element UI's i18n through vue-i18n (falls back to en).
i18n.mergeLocaleMessage('en', elEnLocale);
i18n.mergeLocaleMessage('vi', elViLocale);
i18n.mergeLocaleMessage('zh_CN', elZhCnLocale);
i18n.mergeLocaleMessage('zh_TW', elZhTwLocale);
ElementLocale.i18n((key, value) => i18n.t(key, value));

Vue.config.productionTip = false

// RegisterService Worker
registerServiceWorker();

// CreateVueInstance
new Vue({
  router,
  store,
  i18n,
  render: function (h) { return h(App) }
}).$mount('#app')
