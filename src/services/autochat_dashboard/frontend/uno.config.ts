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
        card: '#1e293b',
        border: '#334155',
        primary: '#38bdf8',
        accent: '#818cf8',
        success: '#34d399',
        warning: '#fbbf24',
        danger: '#f87171',
      },
    },
  },
  shortcuts: {
    'btn-primary': 'px-3 py-1.5 bg-sky-500 hover:bg-sky-400 text-slate-900 rounded-md text-xs font-semibold cursor-pointer transition select-none disabled:opacity-50 disabled:cursor-not-allowed',
    'btn-secondary': 'px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded-md text-xs font-medium cursor-pointer transition select-none',
    'card-panel': 'bg-slate-800/80 border border-slate-700/80 rounded-xl p-4 backdrop-blur-sm',
    'badge-pill': 'px-2 py-0.5 text-xs rounded-full font-mono font-medium',
    'input-dark': 'bg-slate-900/90 border border-slate-700 focus:border-sky-500 rounded-lg px-3 py-1.5 text-slate-100 text-sm outline-none transition',
  },
});
