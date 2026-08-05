import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './locales/en.json';
import zh from './locales/zh.json';

// Persist language choice: LanguageToggle stores 'lang' in localStorage, but
// the previous init hardcoded 'zh' so a page reload always reset to Chinese.
const savedLang = (() => {
  try { return localStorage.getItem('lang'); } catch { return null; }
})();

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    zh: { translation: zh },
  },
  lng: savedLang === 'en' ? 'en' : 'zh',
  fallbackLng: 'zh',
  interpolation: { escapeValue: false },
});

export default i18n;
