import Vue from 'vue';
import VueI18n from 'vue-i18n';
import zhCN from './zh_CN';
import zhTW from './zh_TW';
import en from './en';
import de from './de';
import vi from './vi';
import ptBR from './pt_BR';

Vue.use(VueI18n);

// Always default to English; respect explicit user override via localStorage.
const getDefaultLanguage = () => {
  const savedLang = localStorage.getItem('userLanguage');
  if (savedLang) {
    return savedLang;
  }
  return 'en';
};

const i18n = new VueI18n({
  locale: getDefaultLanguage(),
  fallbackLocale: 'en',
  messages: {
    'zh_CN': zhCN,
    'zh_TW': zhTW,
    'en': en,
    'de': de,
    'vi': vi,
    'pt_BR': ptBR
  }
});

export default i18n;

// Provide method to switch language
export const changeLanguage = (lang) => {
  i18n.locale = lang;
  localStorage.setItem('userLanguage', lang);
  // Notify component language changed
  Vue.prototype.$eventBus.$emit('languageChanged', lang);
};