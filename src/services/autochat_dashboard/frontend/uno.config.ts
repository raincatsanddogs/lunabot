import { defineConfig, presetUno, presetAttributify } from 'unocss';

export default defineConfig({
  presets: [
    presetUno(),
    presetAttributify(),
  ],
  theme: {
    colors: {
      brand: {
        dark: '#0f172a',
        card: '#ffffff',
        border: '#e2e8f0',
        primary: '#0284c7',
        accent: '#6366f1',
        success: '#10b981',
        warning: '#f59e0b',
        danger: '#ef4444',
      },
    },
  },
  shortcuts: {
    'card-panel': 'bg-white border border-slate-200/80 shadow-sm rounded-xl p-5',
    'input-base': 'bg-white border border-slate-200 focus:border-sky-500 focus:ring-2 focus:ring-sky-500/15 text-slate-800 rounded-lg px-3 py-2 text-sm outline-none transition',
    'btn-primary': 'bg-sky-600 hover:bg-sky-700 text-white font-medium rounded-lg px-4 py-2 text-sm shadow-sm transition active:scale-98 cursor-pointer select-none disabled:opacity-50 disabled:cursor-not-allowed',
    'btn-secondary': 'bg-slate-100 hover:bg-slate-200 text-slate-700 font-medium rounded-lg px-3 py-1.5 text-xs transition cursor-pointer select-none border border-slate-200/80 active:scale-98 disabled:opacity-50 disabled:cursor-not-allowed',
    'badge-pill': 'px-2 py-0.5 text-xs rounded-full font-mono font-medium',
    'input-dark': 'input-base',
  },
});
