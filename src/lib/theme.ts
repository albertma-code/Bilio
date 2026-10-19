import type { GlobalThemeOverrides } from 'naive-ui';

export type ThemeChoice = 'coral' | 'graphite';
// Graphite was selected in the visual review; coral remains a dev-only comparison.
const candidate = import.meta.env.DEV ? new URLSearchParams(location.search).get('theme') : null;
export const themeChoice: ThemeChoice = candidate === 'coral' ? 'coral' : 'graphite';
document.documentElement.dataset.bilioTheme = themeChoice;

const primary = themeChoice === 'coral' ? '#bd443b' : '#30343b';
export const theme: GlobalThemeOverrides = {
  common: {
    primaryColor: primary,
    primaryColorHover: themeChoice === 'coral' ? '#a73831' : '#454a53',
    primaryColorPressed: themeChoice === 'coral' ? '#902e28' : '#20242a',
    borderRadius: '6px', fontSize: '13px', heightMedium: '36px',
    textColorBase: '#252a2d',
      textColor1: '#25272c', textColor2: '#555c66', textColor3: '#626973',
      borderColor: '#d9dde2', successColor: '#257347', warningColor: '#865d13', errorColor: '#a52d46',
  },
  Button: { fontWeight: '500' },
};
